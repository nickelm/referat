"""The command center window: the meetings list, the viewer, and the controls.

Phases 1 to 3 of build step 20. Phase 1 was deliberately read-only — the list,
the viewer, the cross-links, the recording buttons and the ambient state — so
that the toolkit question was settled against real meetings with nothing at
risk; phase 2 added tagging and phase 3 labeling, and **this file still writes
nothing itself**. It opens two dialogs, and each of them makes its one kind of
change through a function it does not implement: :mod:`referat.ui.tags` through
`cli.apply_tags`, and :mod:`referat.ui.speakers` through `label.name_speaker`.
The only other thing the window changes is the recorder, and that goes through
the very methods the two hotkeys call.

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
import threading
from typing import Any

from PySide6.QtCore import QSize, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from referat import cli, paths
from referat.state import State
from referat.ui import speakers, tags
from referat.ui.people import PeoplePage
from referat.ui.projects import ProjectsPage
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


def _working_lines(status: str) -> list[str]:
    """What to say about a meeting that has no transcript yet, and why not.

    Two different waits and they are not interchangeable: a recording has not
    finished happening, and a transcription has finished happening and is being
    read. Somebody looking at an empty pane wants to know which.
    """
    if status == "recording":
        return [
            "This meeting is being recorded right now.",
            "Audio is streaming to disk. Transcription starts when you stop it, "
            "and the transcript appears here when it finishes.",
        ]
    return [
        "This meeting is being transcribed right now.",
        "transcript.md is written once, whole, at the end of the pipeline - so an "
        "interrupted run leaves the previous transcript intact rather than half a "
        "new one. There is nothing to show until it lands.",
        "The status bar says which stage it is on.",
    ]


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

        # Zoom and copy as window actions rather than buttons. Qt's own
        # Ctrl+wheel already zoomed a pane and was not enough: it needs a mouse,
        # and a keyboard shortcut is what somebody reaching for bigger text
        # actually reaches for. Ctrl+= as well as Ctrl++, because + is a shifted
        # key on most layouts and every browser accepts both.
        for keys, steps in (("Ctrl++", 1), ("Ctrl+=", 1), ("Ctrl+-", -1), ("Ctrl+0", 0)):
            action = QAction(f"Zoom {steps}", self)
            action.setShortcut(keys)
            action.triggered.connect(lambda _checked=False, n=steps: self.viewer.zoom(n))
            self.addAction(action)

        self.copy_action = QAction("Copy as plain text", self)
        # Ctrl+Shift+C, leaving Ctrl+C to the pane's own selection copy. Both end
        # up plain here, but the pane's is Qt's and this one is the whole
        # document, so they are two operations and want two keys.
        self.copy_action.setShortcut("Ctrl+Shift+C")
        self.copy_action.triggered.connect(self._on_copy)
        self.addAction(self.copy_action)

        self.record_button.clicked.connect(self.app.start_meeting)
        self.pause_button.clicked.connect(self.app.on_toggle_pause)
        self.stop_button.clicked.connect(self.app.stop_meeting)

        self.tag_button = QPushButton("Tags...")
        self.tag_button.setEnabled(False)
        self.tag_button.clicked.connect(self._on_tag)

        # Second, and to the right of the tag button, because that order is the
        # flow rule made visible: tag first, then label, so the gallery a person
        # is offered is already narrowed to the project's people. A nudge and
        # never a gate — an untagged meeting labels perfectly well, with the full
        # gallery, because a missing tag must never cost a name.
        self.label_button = QPushButton("Speakers...")
        self.label_button.setEnabled(False)
        self.label_button.clicked.connect(self._on_label)

        # Third and fourth, and the two the window simply did not have: the
        # sidebar could write notes and delete a meeting and this could not, so
        # anybody using the command center had to keep the extension open for
        # them. Notes before Delete, and Delete last and on its own, because it
        # is the destructive one — the same order the sidebar's row uses.
        self.notes_button = QPushButton("Generate notes...")
        self.notes_button.setEnabled(False)
        self.notes_button.clicked.connect(self._on_notes)

        self.delete_button = QPushButton("Delete...")
        self.delete_button.setEnabled(False)
        self.delete_button.clicked.connect(self._on_delete)

        self.meetings = QTreeWidget()
        self.meetings.setColumnCount(len(COLUMNS))
        self.meetings.setHeaderLabels(list(COLUMNS))
        self.meetings.setRootIsDecorated(False)
        self.meetings.setAlternatingRowColors(True)
        self.meetings.setUniformRowHeights(True)
        # Title takes the slack and every other column asks for exactly what it
        # needs, so the six of them span the window instead of overflowing it.
        # `setStretchLastSection(False)` first, or Qt gives the spare width to
        # Projects and the mode set below is ignored.
        header = self.meetings.header()
        header.setStretchLastSection(False)
        for column in range(len(COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
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

        # The filter row and the two per-meeting buttons belong to the list
        # rather than to the window, which is also the shape phase 6 wants when
        # this pane grows a dashboard beside it. Phase 4 is what forced the
        # distinction: the record buttons are the *recorder* and stay above the
        # tabs, while `Tags...` and `Speakers...` act on a selected meeting and
        # would be a pair of dead buttons on the projects page.
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.addStretch(1)
        actions.addWidget(self.tag_button)
        actions.addWidget(self.label_button)
        actions.addWidget(self.notes_button)
        actions.addSpacing(18)
        actions.addWidget(self.delete_button)

        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.addLayout(filters)
        left.addWidget(self.meetings, 1)
        left.addLayout(actions)
        left_pane = QWidget()
        left_pane.setLayout(left)

        self.viewer = Viewer()
        self.viewer.external_requested.connect(self._on_external_link)
        self.viewer.person_requested.connect(self.open_person)

        # Vertical, and that is the fix for the complaint that the list needed
        # horizontal scrolling. Six columns in the three-sevenths of a window a
        # horizontal splitter left them never fitted, and no amount of resizing
        # policy makes six columns fit in four hundred pixels. Full width they
        # fit with room to spare, and a transcript is a better shape read wide
        # anyway.
        self.meetings_page = QSplitter(Qt.Orientation.Vertical)
        self.meetings_page.addWidget(left_pane)
        self.meetings_page.addWidget(self.viewer)
        self.meetings_page.setStretchFactor(0, 2)
        self.meetings_page.setStretchFactor(1, 3)
        self.meetings_page.setSizes([260, 420])

        controls = QHBoxLayout()
        for button in (self.record_button, self.pause_button, self.stop_button):
            button.setMinimumSize(QSize(92, 30))
            controls.addWidget(button)
        controls.addSpacing(12)
        controls.addWidget(self.state_label)
        controls.addStretch(1)

        # One tab per entity, which is the window this step is building: all three
        # are here from phase 5, and phase 6 puts a Dashboard in front. The
        # recorder's three buttons stay *above* the tabs, because the recorder is
        # not one of the three entities and a Stop button that hid behind a tab
        # would be a recording somebody could not stop from here.
        self.pages = QTabWidget()
        self.projects = ProjectsPage(self.app.config)
        self.people = PeoplePage(self.app.config)
        self.people.meeting_requested.connect(self.open_meeting)
        self.people.project_requested.connect(self.open_project)
        self.pages.addTab(self.meetings_page, "Meetings")
        self.pages.addTab(self.projects, "Projects")
        self.pages.addTab(self.people, "People")
        self.pages.currentChanged.connect(self._on_page_changed)

        # The activity strip, permanent in the status bar so it is visible from
        # every tab. Transcription and `/cleanup` are the two things in Referat
        # that take minutes, and until now both were invisible unless you tailed
        # the log: the tray icon changed colour and that was the whole of it.
        self.activity = QLabel()
        self.activity.setTextFormat(Qt.TextFormat.PlainText)
        self.activity.hide()
        self.activity_bar = QProgressBar()
        self.activity_bar.setMaximumWidth(180)
        self.activity_bar.setTextVisible(False)
        self.activity_bar.hide()
        self.statusBar().addPermanentWidget(self.activity)
        self.statusBar().addPermanentWidget(self.activity_bar)

        layout = QVBoxLayout()
        layout.setContentsMargins(10, 10, 10, 6)
        layout.addLayout(controls)
        layout.addWidget(self.pages, 1)
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

        **Only the page in front is refreshed.** Every page costs a scan of both
        meeting roots, and this runs on every transition; a hidden page is
        brought up to date by :meth:`_on_page_changed` when it is switched to,
        which is the moment before anybody could read a stale figure off it.
        """
        self._reload()
        self._rebuild()
        self._refresh_page(self.pages.currentWidget())

    def _on_page_changed(self, index: int) -> None:
        """Refresh the page being switched to.

        Every page but the meetings list carries figures anything on another page
        can invalidate — the projects page's meeting counts and merged hotword
        list, the people page's whole directory, all moved by tagging a meeting or
        naming a speaker. Each is refreshed on arrival rather than kept live,
        which is the moment before anybody could read a stale figure off it, and
        the projects page keeps whatever is typed into its form across one: see
        :meth:`referat.ui.projects.ProjectsPage._fill_list`.
        """
        self._refresh_page(self.pages.widget(index))

    def _refresh_page(self, page: QWidget | None) -> None:
        """Re-read one page, if it is one of the ones that reads anything."""
        if page in (self.projects, self.people):
            page.refresh()

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
            for button in (self.label_button, self.notes_button, self.delete_button):
                button.setEnabled(False)
            return
        self._selected = str(item.data(0, ID_ROLE))
        # Disabled rather than hidden when there is nobody left to name, for the
        # reason the three recording buttons are: a button that moves is a button
        # you have to look for. The count is the listing's own `unnamed`, which is
        # `voices.unknown_speakers` — so an echo cluster is not offered here
        # either, and this enables on exactly what the dialog would show.
        self.label_button.setEnabled(bool(self._unnamed(self._selected)))
        meeting = self._meeting(self._selected)
        live = meeting is not None and meeting["status"] in ("recording", "transcribing")
        # Notes need a transcript to read; deleting refuses a live meeting in
        # `cli.delete_meeting` anyway, and disabling here is that refusal said
        # before it has to be. A staged meeting keeps its button and is answered
        # with a sentence, because *why* is the useful part there.
        self.notes_button.setEnabled(bool(meeting and meeting["transcript"]) and not live)
        self.delete_button.setEnabled(not live)
        self._show(self._selected)

    def _meeting(self, meeting_id: str | None) -> dict[str, Any] | None:
        """One row out of the listing this window is holding. Reads no file."""
        if meeting_id is None:
            return None
        return next(
            (m for m in self._document["meetings"] if m["id"] == meeting_id), None
        )

    def _unnamed(self, meeting_id: str) -> list[str]:
        """The speakers this meeting is still waiting on, out of the listing. Reads nothing."""
        return next(
            (m["unnamed"] for m in self._document["meetings"] if m["id"] == meeting_id), []
        )

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
        meeting = self._meeting(meeting_id)
        carried = meeting["tags"] if meeting else []
        if tags.open_for(self, self.app.config, meeting_id, list(carried)):
            self.refresh()

    # --- Labeling -----------------------------------------------------------

    def _on_label(self) -> None:
        """Open the labeling dialog on the selected meeting. The second thing this writes.

        Takes the id off `self._selected` for the same reason :meth:`_on_tag`
        does: `_rebuild` destroys every `QTreeWidgetItem`, and a transition
        arriving through the shell's `Bridge` can run it while the dialog is up.

        Refreshes only when a name was actually filed. Unlike the tag picker there
        is nothing a dismissed dialog can have created — naming is the only write
        it makes, and it makes it immediately.
        """
        meeting_id = self._selected
        if meeting_id is None:
            return
        if speakers.open_for(self, self.app.config, meeting_id, self.app):
            self.refresh()

    def _show(self, meeting_id: str) -> None:
        """Fill the viewer from the CLI's own documents, plus `notes.md` itself.

        A meeting that is still being recorded or transcribed is answered with
        :meth:`referat.ui.viewer.Viewer.show_working` rather than with the two
        empty panes it used to draw. `transcript.md` is written whole at the very
        end of the pipeline — deliberately, so an interrupted run leaves the
        previous one intact — so there is genuinely nothing to show yet, and an
        empty pane says that in the same voice as a broken window.
        """
        from referat.meeting import resolve_meeting

        row = self._meeting(meeting_id)
        if row is not None and row["status"] in ("recording", "transcribing"):
            self.viewer.show_working(
                f"{meeting_id} - {str(row['status']).replace('_', ' ')}",
                _working_lines(row["status"]),
            )
            self.setWindowTitle(f"Referat - {meeting_id}")
            return

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
        """A link in the notes that is neither a timestamp nor a name.

        `notes.md` is written by `/cleanup` and may carry a real URL, which is the
        one thing this window hands outside itself.
        """
        QDesktopServices.openUrl(url)

    # --- Notes and deletion -------------------------------------------------

    def _on_notes(self) -> None:
        """Run `/cleanup` over the selected meeting, on a thread.

        The two things the sidebar could do and this window could not are this
        and :meth:`_on_delete`; anybody living in the command center had to keep
        the extension open for them.

        On a thread, because a cleanup pass takes a minute or two and blocking
        the GUI thread here would freeze the *recorder's* window — this process
        owns both. What it reports goes through :mod:`referat.progress` to the
        activity strip, and the result comes back through `App.notify`, which
        since the 2026-09-03 crash is the one safe way to reach Qt from a thread.

        `cli.write_notes` is the operation and this does not sequence its two
        halves: spawning the pass and recording `notes_written` belong together
        and belong to the CLI, exactly as tagging belongs to `cli.apply_tags`.
        """
        meeting_id = self._selected
        if meeting_id is None:
            return
        meeting = self._meeting(meeting_id)
        if meeting is not None and meeting["staged"]:
            # `/cleanup` runs with cwd at the meetings folder and is handed a
            # meeting id; a staged meeting is not in that folder, so the pass
            # would look for a transcript that is not there.
            QMessageBox.information(
                self,
                "Generate notes",
                f"{meeting_id} is still in staging, so /cleanup cannot reach it.\n\n"
                f"It gets there once its audio is released - either a rerun that "
                f"comes out clean, or `referat promote {meeting_id} --release-audio`.",
            )
            return
        if meeting is not None and not meeting["transcript"]:
            QMessageBox.information(
                self,
                "Generate notes",
                f"{meeting_id} has no transcript.md yet. Notes are written from the "
                f"transcript, so there is nothing to read.",
            )
            return

        self.notes_button.setEnabled(False)
        threading.Thread(
            target=self._write_notes, args=(meeting_id,), name="notes", daemon=True
        ).start()

    def _write_notes(self, meeting_id: str) -> None:
        """The worker half of :meth:`_on_notes`. Touches no widget.

        Not one line of Qt in here, which is the whole discipline the heap
        corruption taught: `App.notify` marshals through the shell's `Bridge`,
        and the progress reports go through a registry that knows nothing about
        toolkits.
        """
        try:
            outcome = cli.write_notes(self.app.config, meeting_id)
            self.app.notify(
                outcome.message if outcome.ok else f"Notes for {meeting_id}: {outcome.message}"
            )
        except Exception:
            log.exception("could not generate notes for %s", meeting_id)
            self.app.notify(f"Could not generate notes for {meeting_id} - see the log.")

    def _on_delete(self) -> None:
        """Delete the selected meeting, behind the warning Python wrote.

        The modal is shown *before* the command runs, so there is no outcome to
        quote — the same exception the projects and people pages are. What it
        says is `cli.delete_warning`'s text and not this window's: whether the
        deletion is real depends on whether the folder is synced, and the
        voiceprints stay either way. Said in one place so the prompt and this
        cannot come to describe an irreversible act differently.
        """
        meeting_id = self._selected
        if meeting_id is None:
            return
        from referat.meeting import resolve_meeting

        meeting, why = resolve_meeting(self.app.config, meeting_id)
        if meeting is None:
            QMessageBox.warning(self, "Delete meeting", why)
            return
        answer = QMessageBox.question(
            self,
            "Delete meeting",
            f"Delete {meeting.id}?\n\n{cli.delete_warning(self.app.config, meeting)}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        outcome = cli.delete_meeting(self.app.config, meeting_id)
        if not outcome.ok:
            QMessageBox.warning(self, "Delete meeting", outcome.message)
            return
        log.info("%s", outcome.message)
        self._selected = None
        self.refresh()

    def _on_copy(self) -> None:
        """Put the visible document on the clipboard as plain text."""
        what = self.viewer.copy_current()
        if what:
            self.statusBar().showMessage(f"Copied {what} as plain text.", 4000)

    # --- What the slow things are doing --------------------------------------

    def on_progress(self, jobs: list[Any]) -> None:
        """Render the running jobs. **On the GUI thread only** — the shell marshals.

        Nothing is shown when nothing is running, rather than an idle bar: a
        progress widget that is always there stops being read.
        """
        if not jobs:
            # Cleared as well as hidden, or the next job flashes the last one's
            # phase for the frame between `show` and the first `setText`.
            self.activity.clear()
            self.activity.hide()
            self.activity_bar.hide()
            return
        job = jobs[0]
        extra = f"  (+{len(jobs) - 1} more)" if len(jobs) > 1 else ""
        self.activity.setText(f"{job.title}: {job.phase}{extra}")
        self.activity.show()
        if job.fraction is None:
            # A busy indicator, which is what a range of zero means to Qt, and
            # the honest rendering of a phase with no measurable end. Zero
            # percent would be a claim about progress.
            self.activity_bar.setRange(0, 0)
        else:
            self.activity_bar.setRange(0, 100)
            self.activity_bar.setValue(int(job.fraction * 100))
        self.activity_bar.show()

    # --- Going somewhere ----------------------------------------------------

    def open_person(self, name: str) -> None:
        """Show one person, from a speaker label or a `[[Wikilink]]` being clicked.

        The whole argument for a window rather than a column: a name in a document
        is a thing you can follow. A name nobody is filed under still opens the
        page, which says so — see :meth:`referat.ui.people.PeoplePage.select`.
        """
        self.pages.setCurrentWidget(self.people)
        self.people.select(name)

    def open_meeting(self, meeting_id: str) -> None:
        """Show one meeting on the Meetings tab, from wherever it was clicked.

        The filters are cleared first: a meeting reached by following a link from
        somewhere else must not be hidden by a search somebody typed ten minutes
        ago, which would look exactly like a link that did nothing.
        """
        for widget, clear in (
            (self.untagged_only, lambda: self.untagged_only.setChecked(False)),
            (self.search, self.search.clear),
        ):
            widget.blockSignals(True)
            clear()
            widget.blockSignals(False)
        self._selected = meeting_id
        self.pages.setCurrentWidget(self.meetings_page)
        self.refresh()

    def open_project(self, pid: str) -> None:
        """Show one project on the Projects tab."""
        self.pages.setCurrentWidget(self.projects)
        self.projects.select_project(pid)

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
