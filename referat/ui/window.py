"""The command center window: the tabs, the meetings list, the viewer, the controls.

Build step 20, phases 1 to 6. Phase 1 was deliberately read-only — the list, the
viewer, the cross-links, the recording buttons and the ambient state — so that
the toolkit question was settled against real meetings with nothing at risk;
phase 2 added tagging, phase 3 labeling, phase 4 the projects page, phase 5
people, and phase 6 put a **dashboard in front of all of them**, because the
meetings list is an inventory and the question somebody opens this window to ask
is whether anything is waiting for them.

**This file still implements no rule of its own.** It opens two dialogs, and each
makes its one kind of change through a function it does not implement:
:mod:`referat.ui.tags` through `cli.apply_tags`, and :mod:`referat.ui.speakers`
through `label.name_speaker`. Its own two writes — notes and deletion — go the
same way, through `cli.write_notes` and `cli.delete_meeting`. The only other
thing it changes is the recorder, and that goes through the very methods the two
hotkeys call.

**It calls the CLI's functions, never its subprocess.** `list_document`,
`show_document` and `transcript_document` are the same builders `referat list
--json` and friends print, so what this window says about a meeting and what the
prompt says about it cannot come apart. It reads exactly one file for itself — `notes.md`,
which is prose to render and not a record to interpret — and no `meta.json`, no
`voices.json`, no `projects.json`.

**Qt widgets rather than QtWebEngine.** Decided here against the criteria step 20
wrote down, and the reasoning is in `TODO.md`; the short form is that
`QTextBrowser` renders Markdown, holds anchors and answers a custom URL scheme,
which is every criterion this phase had, and that the one argument for the web
view — reusing the VS Code sidebar's own page — was an argument for keeping the
column this step exists to escape. That sidebar was deleted at step 23.
"""

from __future__ import annotations

import datetime as dt
import logging
import queue
import threading
from typing import Any

from PySide6.QtCore import QSize, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QIcon
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

from referat import cli, paths, progress
from referat.state import State
from referat.ui import icons, lists, speakers, tags
from referat.ui.actions import ActionsPage
from referat.ui.activity import ActivityPage
from referat.ui.dashboard import DashboardPage
from referat.ui.people import PeoplePage
from referat.ui.projects import ProjectsPage
from referat.ui.rows import status_text, tags_text
from referat.ui.viewer import Viewer

log = logging.getLogger(__name__)

COLUMNS = ("Meeting", "Title", "Duration", "Status", "Unnamed", "Projects")
ID_ROLE = Qt.ItemDataRole.UserRole
"""The meeting id, carried on the row so selection never parses a cell back."""

TAG_NUDGE = "Tag this..."
"""What an untagged row shows where its projects would be.

Text and not a button: `setItemWidget` fights `setUniformRowHeights(True)`, and
the row is already double-clickable. It is also why this lives here rather than
inside :func:`referat.ui.rows.tags_text`, which stays a pure formatter — a call
to action is not a rendering of a tag list.
"""


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
        self._actions: dict[str, Any] = {
            "owner": "",
            "complaint": "",
            "items": [],
            "orphans": [],
            "counts": {},
        }
        """The action items, read once per refresh and handed to two surfaces.

        Beside `_document` rather than inside it because the two are built by
        different functions and `list_document` is a published shape that
        `referat list --json` prints. Both are *handed* to the dashboard and to the
        actions page, which is what keeps a sixth tab from costing a sixth scan
        of both meeting roots.
        """
        self._selected: str | None = None
        self._notes_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self._notes_worker: threading.Thread | None = None
        self._queued: set[tuple[str, str]] = set()
        """What is already on the claude queue, so a double click queues once.

        `(kind, target)` pairs, matching the queue: a day summary and a
        meeting's notes are different jobs even where the strings collide.
        """

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

        # Two flavours and two keys, because the paste target decides which is
        # wanted: Markdown source pastes into Claude, formatted text pastes into
        # Google Docs with real headings and bullets. One clipboard carrying both
        # would decide for the paste — Word, Docs and Outlook all prefer an HTML
        # flavour when one is there. Ctrl+Alt+C rather than anything on Ctrl+F,
        # which a transcript viewer wants for a find one day.
        self.copy_action = QAction("Copy as Markdown", self)
        self.copy_action.setShortcut("Ctrl+Shift+C")
        self.copy_action.triggered.connect(lambda _checked=False: self._on_copy(False))
        self.addAction(self.copy_action)
        self.copy_formatted_action = QAction("Copy as formatted text", self)
        self.copy_formatted_action.setShortcut("Ctrl+Alt+C")
        self.copy_formatted_action.triggered.connect(lambda _checked=False: self._on_copy(True))
        self.addAction(self.copy_formatted_action)

        self.record_button.clicked.connect(self.app.start_meeting)
        self.pause_button.clicked.connect(self.app.on_toggle_pause)
        self.stop_button.clicked.connect(self.app.stop_meeting)

        # The three transport buttons carry the recorder's own colours — the
        # ones the notification-area icon uses — so red means recording in the
        # window and in the taskbar without either being taught the other's
        # vocabulary. Every other icon in this window is drawn in the palette's
        # text colour instead: these three are the only ones that mean a state.
        self.record_button.setIcon(icons.glyph("record", icons.COLORS[State.RECORDING]))
        self.stop_button.setIcon(icons.glyph("stop", icons.COLORS[State.STOPPED]))
        self._on_pause_icon(State.IDLE)

        # Build step 23's two, and they come first because they come first in a
        # meeting's life: everything else here acts on a transcript, and these
        # two act on the *audio* it was made from. Both are disabled for almost
        # every meeting, which is honest rather than untidy — the audio is
        # normally gone, and a meeting that still has it is a meeting waiting on
        # one of exactly these two decisions.
        self.rerun_button = QPushButton("Re-transcribe...")
        self.rerun_button.setIcon(self._glyph("redo"))
        self.rerun_button.setEnabled(False)
        self.rerun_button.setToolTip(
            "Transcribe this meeting again from the audio it kept. Minutes of GPU, "
            "and the speakers are renumbered from scratch."
        )
        self.rerun_button.clicked.connect(self._on_rerun)

        self.promote_button = QPushButton("Promote...")
        self.promote_button.setIcon(self._glyph("promote"))
        self.promote_button.setEnabled(False)
        self.promote_button.setToolTip(
            "Move this meeting out of staging into the meetings folder, deleting "
            "the audio it still holds. Nothing else can reach a staged meeting."
        )
        self.promote_button.clicked.connect(self._on_promote)

        self.tag_button = QPushButton("Tags...")
        self.tag_button.setIcon(self._glyph("tag"))
        self.tag_button.setEnabled(False)
        self.tag_button.clicked.connect(self._on_tag)

        # Second, and to the right of the tag button, because that order is the
        # flow rule made visible: tag first, then label, so the gallery a person
        # is offered is already narrowed to the project's people. A nudge and
        # never a gate — an untagged meeting labels perfectly well, with the full
        # gallery, because a missing tag must never cost a name.
        self.label_button = QPushButton("Speakers...")
        self.label_button.setIcon(self._glyph("person"))
        self.label_button.setEnabled(False)
        self.label_button.clicked.connect(self._on_label)

        # Third and fourth, and the two the window simply did not have until
        # 2026-09-03: the VS Code sidebar could write notes and delete a meeting
        # and this could not, so anybody using the command center had to keep the
        # extension open for them. Notes before Delete, and Delete last and on its
        # own, because it is the destructive one.
        self.notes_button = QPushButton("Generate notes...")
        self.notes_button.setIcon(self._glyph("page"))
        self.notes_button.setEnabled(False)
        self.notes_button.clicked.connect(self._on_notes)

        self.notes_all_button = QPushButton("Notes for all...")
        # A list rather than a second page: the two buttons do the same thing to
        # different numbers of meetings, and two identical icons side by side
        # would make that the one distinction you cannot see.
        self.notes_all_button.setIcon(self._glyph("list"))
        self.notes_all_button.setToolTip(
            "Queue /cleanup for every promoted meeting that has a transcript and no "
            "notes. One at a time, in date order."
        )
        self.notes_all_button.clicked.connect(self._on_notes_all)

        self.delete_button = QPushButton("Delete...")
        # The one icon here that is not the palette's text colour and does not
        # mean a recorder state: the destructive button was already last and
        # spaced away from the others, and this is that separation said again in
        # the one place somebody looks before clicking.
        self.delete_button.setIcon(icons.glyph("trash", icons.LIFECYCLE["failed"]))
        self.delete_button.setEnabled(False)
        self.delete_button.clicked.connect(self._on_delete)

        self.meetings = QTreeWidget()
        self.meetings.setColumnCount(len(COLUMNS))
        self.meetings.setHeaderLabels(list(COLUMNS))
        self.meetings.setRootIsDecorated(False)
        lists.stripe(self.meetings)
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
        actions.addWidget(self.rerun_button)
        actions.addWidget(self.promote_button)
        actions.addSpacing(18)
        actions.addWidget(self.tag_button)
        actions.addWidget(self.label_button)
        actions.addWidget(self.notes_button)
        actions.addWidget(self.notes_all_button)
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
        # The viewer writes the sentence, this shows it: it is the half that
        # knows whether a selection or the whole document went, and the status
        # bar is the window's.
        self.viewer.copied.connect(lambda message: self.statusBar().showMessage(message, 4000))

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
        # are here from phase 5, and phase 6 put the Dashboard in front of them.
        # The recorder's three buttons stay *above* the tabs, because the recorder
        # is not one of the three entities and a Stop button that hid behind a tab
        # would be a recording somebody could not stop from here.
        self.pages = QTabWidget()
        self.dashboard = DashboardPage()
        self.dashboard.meeting_requested.connect(self.open_meeting)
        # The day summary's own links, onto the same two slots the viewer's go to:
        # a name means one thing wherever it is rendered, and so does a URL.
        self.dashboard.person_requested.connect(self.open_person)
        self.dashboard.external_requested.connect(self._on_external_link)
        self.dashboard.action_requested.connect(self.open_action)
        self.dashboard.summary_requested.connect(self._on_summarize)
        self.actions_page = ActionsPage()
        self.actions_page.meeting_requested.connect(self.open_meeting)
        self.actions_page.person_requested.connect(self.open_person)
        self.projects = ProjectsPage(self.app.config)
        # The recap's own links and its button. A citation opens the meeting it
        # came from, a name opens the person, and *Recap...* joins the claude
        # queue rather than growing a worker on the projects page: one rate
        # limit, one folder, which is the argument that made it a queue at all.
        self.projects.meeting_requested.connect(self.open_meeting)
        self.projects.person_requested.connect(self.open_person)
        self.projects.external_requested.connect(self._on_external_link)
        self.projects.recap_requested.connect(self._on_recap)
        self.people = PeoplePage(self.app.config)
        self.people.meeting_requested.connect(self.open_meeting)
        self.people.project_requested.connect(self.open_project)
        self.people.actions_requested.connect(self.open_person_actions)
        self.activity_page = ActivityPage()
        # First, and therefore the tab the window opens on: the meetings list is
        # an inventory of everything there has ever been, and the question
        # somebody opens this window to ask is whether anything is waiting for
        # them. Being first is the whole of phase 6's claim.
        self.pages.addTab(self.dashboard, self._glyph("dashboard"), "Dashboard")
        self.pages.addTab(self.meetings_page, self._glyph("list"), "Meetings")
        # Third, and not a fourth entity: an action item is a derivative of one
        # meeting's notes, and activating a row sends you one tab to the left.
        # Projects and People are joins *across* meetings and belong together on
        # the far side of it.
        self.pages.addTab(self.actions_page, self._glyph("check"), "Actions")
        self.pages.addTab(self.projects, self._glyph("tag"), "Projects")
        self.pages.addTab(self.people, self._glyph("person"), "People")
        # Last, because it is about the machine rather than about one of the
        # three entities — the same reason the recorder's buttons sit above the
        # tabs rather than inside one.
        self.pages.addTab(self.activity_page, self._glyph("pulse"), "Activity")
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

    # --- Icons --------------------------------------------------------------

    def _glyph(self, name: str) -> QIcon:
        """One glyph in this window's own text colour.

        Taken from the palette rather than hard-coded, so a dark theme gets light
        icons instead of black ones on a dark tab bar. Read once per icon at
        construction: :func:`referat.ui.icons.glyph` caches on the colour, so a
        theme changed while the window is open keeps the icons it has — which is
        the price of not repainting eleven pixmaps on every refresh.
        """
        return icons.glyph(name, self.palette().windowText().color().name())

    def _on_pause_icon(self, state: State) -> None:
        """Pause bars, or a play triangle when the meeting is already paused.

        One button meaning two things is exactly the situation the label already
        handles by changing from *Pause* to *Resume*; an icon that stayed at two
        bars while the word said Resume would be the one part of the button that
        was wrong.
        """
        paused = state is State.PAUSED
        self.pause_button.setIcon(
            icons.glyph(
                "play" if paused else "pause",
                icons.COLORS[State.RECORDING if paused else State.PAUSED],
            )
        )

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
        so it learns transitions by callback. Polling is what a surface in another
        process has to do, and this one is not in another process.

        Split from :meth:`_rebuild` so the filter widgets can redraw without
        rescanning both meeting roots on every keystroke.

        **Only the page in front is refreshed.** Every page costs a scan of both
        meeting roots, and this runs on every transition; a hidden page is
        brought up to date by :meth:`_on_page_changed` when it is switched to,
        which is the moment before anybody could read a stale figure off it.

        The dashboard is exempt rather than an exception: it reads nothing, and is
        handed the listing this method has just paid for, so keeping it current
        even while it is behind another tab costs a redraw and no I/O at all.
        """
        self._reload()
        self._rebuild()
        self.dashboard.set_document(self._document)
        self.dashboard.set_actions(self._actions)
        self.dashboard.set_days(self._read_days())
        self.actions_page.set_document(self._actions)
        self.people.set_actions(self._actions)
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
        if page in (self.projects, self.people, self.activity_page):
            page.refresh()

    def _reload(self) -> None:
        """Re-read `list_document` and the action items, keeping the old ones on failure.

        The action items are built **from the listing just read** rather than
        from a second scan: :func:`referat.cli.actions_document` is pure over a
        `list_document` when it is handed one, exactly as `cli.pending` is. So
        the Actions tab and the dashboard's box together cost one `notes.md` per
        meeting and no extra walk of either meeting root.

        Two reads and two `except`s, because they fail independently: an
        unreadable `notes.md` must not cost the meetings list, and a meetings
        folder that has gone away must not leave a stale action list looking
        current.
        """
        try:
            self._document = cli.list_document(self.app.config)
        except Exception:
            # A meetings folder that has gone away, a meta.json mid-write. The
            # window keeps the list it had rather than emptying itself.
            log.exception("could not read the meetings")
        try:
            self._actions = cli.actions_document(self.app.config, self._document)
        except Exception:
            log.exception("could not read the action items")

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
                status_text(meeting),
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
                    status_text(meeting),
                    str(len(meeting["unnamed"])) if meeting["unnamed"] else "",
                    carried or TAG_NUDGE,
                ]
            )
            # The lifecycle as a colour beside the word for it, and a *ring*
            # rather than a disc for a run that lost a channel — the tray's own
            # rule, applied to a row. It goes on the Status column and not on the
            # first one, because it is a picture of that cell and not a picture
            # of the meeting.
            item.setIcon(3, icons.status_dot(meeting["status"], bool(meeting["missing_channels"])))
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
            for button in (
                self.rerun_button,
                self.promote_button,
                self.label_button,
                self.notes_button,
                self.delete_button,
            ):
                button.setEnabled(False)
            return
        self._selected = str(item.data(0, ID_ROLE))
        # Disabled rather than hidden when there is nobody left to name, for the
        # reason the three recording buttons are: a button that moves is a button
        # you have to look for. The count is the listing's own `unnamed`, which is
        # `voices.unknown_speakers` — so an echo or noise cluster is not offered
        # here either, and this enables on exactly what the dialog would show.
        self.label_button.setEnabled(bool(self._unnamed(self._selected)))
        meeting = self._meeting(self._selected)
        live = meeting is not None and meeting["status"] in ("recording", "transcribing")
        # Notes need a transcript to read; deleting refuses a live meeting in
        # `cli.delete_meeting` anyway, and disabling here is that refusal said
        # before it has to be. A staged meeting keeps its button and is answered
        # with a sentence, because *why* is the useful part there.
        self.notes_button.setEnabled(bool(meeting and meeting["transcript"]) and not live)
        self.delete_button.setEnabled(not live)
        # `audio` is `kept` for exactly the meetings a rerun has something to run
        # from, and `staged` for exactly the ones that have not made it into the
        # meetings folder — which is the union of a failed gate, an audio kept on
        # request, and the rare promotion that could not move the folder. Two
        # conditions rather than one, because they are two different questions
        # even though they answer the same way nearly always.
        kept = bool(meeting and meeting["audio"] == "kept")
        self.rerun_button.setEnabled(kept and not live)
        self.promote_button.setEnabled(bool(meeting and meeting["staged"]) and not live)
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

        Refreshes only when something was actually written. Unlike the tag picker
        there is nothing a dismissed dialog can have created — a name and a noise
        verdict are its only writes, and it makes each immediately.
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

        Two of the four things the VS Code sidebar could do and this window could
        not: this and :meth:`_on_delete` closed on 2026-09-03, and
        :meth:`_on_rerun` and :meth:`_on_promote` closed the rest at step 23,
        which is what let the extension be deleted.

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

        self._enqueue_notes([(progress.NOTES, meeting_id)])

    def _on_notes_all(self) -> None:
        """Queue every promoted meeting that has a transcript and no notes.

        The answer to *is there a way to launch them all* — and there is no
        reason not to, since `/cleanup` reads a transcript and writes a file
        beside it. Date order, oldest first, so the backlog is worked through the
        way it accumulated.

        Staged meetings are skipped without comment: `/cleanup` runs with cwd at
        the meetings folder and cannot reach one. So are meetings that already
        have notes — re-running one is a deliberate act and stays the single
        button's job.

        Which meetings those are is :func:`referat.cli.pending`'s answer since
        phase 6, not this method's. The dashboard shows the same queue, and a
        queue whose count came from one predicate and whose button drained
        another would be wrong in the way that is hardest to notice: it would
        look right.
        """
        wanted = [m["id"] for m in cli.pending(self._document)["notes"]]
        if not wanted:
            QMessageBox.information(
                self,
                "Notes for all",
                "Every promoted meeting with a transcript already has notes.",
            )
            return
        answer = QMessageBox.question(
            self,
            "Notes for all",
            f"Queue /cleanup for {len(wanted)} meeting(s)?\n\n"
            f"They run one at a time, oldest first, and each one spawns claude "
            f"under your own Claude Code login. The activity tab shows the queue.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._enqueue_notes([(progress.NOTES, mid) for mid in wanted])

    def _read_days(self) -> dict[str, str]:
        """Today's and yesterday's summaries, as text, for the dashboard's box.

        Read here rather than in the page, which keeps the dashboard's promise
        that it opens no file — the same arrangement as the two documents it is
        handed. Two days because those are the two the box is for: what has
        happened so far, and what happened before it. A day with no summary maps
        to the empty string rather than being left out, so the picker can still
        offer it and the button still has something to write.
        """
        today = dt.date.today()
        days: dict[str, str] = {}
        for offset in (0, 1):
            day = (today - dt.timedelta(days=offset)).isoformat()
            path = cli.day_summary_path(self.app.config, day)
            try:
                days[day] = path.read_text(encoding="utf-8")
            except OSError:
                days[day] = ""
        return days

    def _on_summarize(self, day: str) -> None:
        """Queue a `/standup` for one day, on the same worker the notes use.

        One rate limit and one folder, which is the argument that made notes a
        queue in the first place; a day summary racing a cleanup pass would be
        two `claude` processes writing into the same directory.
        """
        self._enqueue_notes([(progress.DAY, day)])

    def _on_recap(self, project_id: str) -> None:
        """Queue a `/recap` for one project, on the same worker the notes use.

        The projects page's *Recap...* lands here rather than on that page's own
        doc-job thread, because a recap is a `claude` pass and not a network
        call: it is the third kind of job on this queue for the reason the day
        summary was the second. The result reaches the page the way every
        outcome does — `App.notify`, then a refresh — and the page re-reads the
        recap document when it is drawn.
        """
        self._enqueue_notes([(progress.RECAP, project_id)])

    def _job_key(self, kind: str, target: str) -> str:
        """The :mod:`referat.progress` key for one queued job, whichever kind it is."""
        from referat import notes as notes_module

        if kind == progress.DAY:
            return notes_module.day_progress_key(target)
        if kind == progress.RECAP:
            return notes_module.recap_progress_key(target)
        return notes_module.progress_key(target)

    def _enqueue_notes(self, jobs: list[tuple[str, str]]) -> None:
        """Put `(kind, target)` jobs on the claude queue and start the worker.

        **One worker and a FIFO**, rather than a thread per job. Each pass is a
        `claude` subprocess doing real work, and ten at once would be ten
        subprocesses competing for the same rate limit and writing into the same
        folder. Sequential is also what makes the queue legible.

        The queue carries a *pair* rather than a bare meeting id, which is what
        let the day summary join it rather than grow a second worker beside it:
        the two write into the same folder against the same rate limit, so they
        are the same queue by the argument that made it a queue at all. `kind` is
        :data:`referat.progress.NOTES` or :data:`referat.progress.DAY`, and the
        loop dispatches on it.

        Each job is announced to :mod:`referat.progress` as *queued* the moment it
        is accepted, so the activity tab shows the whole backlog rather than only
        the one in flight. The generator re-announces the same key when it
        actually starts, which replaces the queued entry in place.
        """
        fresh = [job for job in jobs if job not in self._queued]
        if not fresh:
            return
        for kind, target in fresh:
            self._queued.add((kind, target))
            progress.begin(self._job_key(kind, target), kind, target, "queued")
            self._notes_queue.put((kind, target))
        if self._notes_worker is None or not self._notes_worker.is_alive():
            self._notes_worker = threading.Thread(
                target=self._notes_loop, name="notes", daemon=True
            )
            self._notes_worker.start()

    def _notes_loop(self) -> None:
        """Run queued cleanup passes one at a time. Touches no widget.

        Not one line of Qt in here, which is the discipline the heap corruption
        taught: `App.notify` marshals through the shell's `Bridge` and the
        progress reports go through a registry that knows nothing about toolkits.

        The thread ends when the queue empties rather than idling forever, and
        :meth:`_enqueue_notes` starts a new one — a daemon thread blocked on a
        `get()` for the life of the process is a thing that shows up in a stack
        dump and puzzles somebody later.
        """
        from referat import notes as notes_module

        # Held for the whole queue rather than per pass, and dropped in a
        # `finally` so an exception on the way out cannot leave the machine
        # awake forever. `claude` is a subprocess that can think for minutes with
        # nobody touching the keyboard, which is precisely the idle timer's case;
        # the reason is separate from the recorder's so neither can drop the
        # other's hold. See :mod:`referat.power`.
        self.app.power.want("notes", True)
        try:
            self._drain_notes(notes_module)
        finally:
            self.app.power.want("notes", False)

    def _drain_notes(self, notes_module: Any) -> None:
        """The queue loop itself, with the sleep hold already taken.

        Dispatches on the job's kind. Both branches go through a guarded `cli`
        function and neither knows anything about how the pass is run.
        """
        while True:
            try:
                kind, target = self._notes_queue.get_nowait()
            except queue.Empty:
                return
            what = {progress.DAY: "Day summary", progress.RECAP: "Recap"}.get(kind, "Notes")
            try:
                if kind == progress.DAY:
                    outcome = cli.write_day_summary(self.app.config, target)
                elif kind == progress.RECAP:
                    outcome = cli.write_recap(self.app.config, target)
                else:
                    outcome = cli.write_notes(self.app.config, target)
                self.app.notify(
                    outcome.message if outcome.ok else f"{what} for {target}: {outcome.message}"
                )
            except Exception:
                log.exception("could not run %s for %s", kind, target)
                self.app.notify(f"Could not write {what.lower()} for {target} - see the log.")
            finally:
                # Whatever happened, the key must go: the generator ends its own,
                # but a failure before it began would leave the *queued* entry on
                # the activity tab forever.
                progress.end(self._job_key(kind, target))
                self._queued.discard((kind, target))
                self._notes_queue.task_done()

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

    def _on_rerun(self) -> None:
        """Transcribe the selected meeting again, from the audio it kept.

        The last thing the sidebar could do and this window could not, and the
        one it did by opening a terminal — which was the right answer *there*,
        since the extension is another process and `referat rerun` loads the
        model in a third. Here it is neither: this window lives inside the tray,
        which already owns a transcription thread, the state machine and the job
        count. So the work goes onto that thread through
        :meth:`referat.tray.App.rerun_meeting`, and this asks once and reports.

        Nothing is awaited. The pipeline announces itself through
        :mod:`referat.progress`, which is the activity strip and the Activity
        tab, and says it is finished through `App.notify` — the same two places a
        recording stopped by the hotkey reports through, which is the point.

        The confirmation is not ceremony. A rerun renumbers the speakers from
        scratch and replaces `speaker_names` wholesale on success: every name is
        looked up again in the known-voices database, so a voice that matched
        will match again, and one whose print sits under the threshold comes back
        a number. That is a real cost and it is worth naming before it is paid.
        """
        meeting_id = self._selected
        if meeting_id is None:
            return
        answer = QMessageBox.question(
            self,
            "Re-transcribe",
            f"Transcribe {meeting_id} again from its audio?\n\n"
            f"Minutes of GPU, and diarization clusters from scratch - the speakers "
            f"are renumbered and their names are looked up again in the known-voices "
            f"database. A voice whose print no longer matches comes back as a number.\n\n"
            f"The current transcript.md stays until the new one is written whole, "
            f"so an interrupted run costs nothing.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        ok, message = self.app.rerun_meeting(meeting_id)
        if not ok:
            QMessageBox.warning(self, "Re-transcribe", message)
            return
        log.info("%s", message)
        self.statusBar().showMessage(message.capitalize(), 6000)
        self.refresh()

    def _on_promote(self) -> None:
        """Move the selected meeting out of staging, releasing whatever audio it holds.

        The off-ramp for a meeting the quality gate refused, and for one
        `[transcription].keep_audio` kept on purpose. Both are stuck in the same
        place for the same reason — `promote_meeting` will not move a folder
        holding a WAV, because no WAV may ever reach a synced meetings folder —
        and both are answered by the same decision: accept the transcript and let
        the recording go.

        Behind the warning Python wrote, and shown *before* the command runs, so
        there is no outcome to quote — the same exception :meth:`_on_delete` is,
        and `cli.promote_warning` is the one text the prompt's refusal and this
        modal both speak in.
        """
        meeting_id = self._selected
        if meeting_id is None:
            return
        from referat.meeting import resolve_meeting

        meeting, why = resolve_meeting(self.app.config, meeting_id)
        if meeting is None:
            QMessageBox.warning(self, "Promote out of staging", why)
            return
        answer = QMessageBox.question(
            self,
            "Promote out of staging",
            f"Promote {meeting.id}?\n\n{cli.promote_warning(self.app.config, meeting)}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        outcome = cli.promote_meeting(self.app.config, meeting_id, release_audio=True)
        if not outcome.ok:
            QMessageBox.warning(self, "Promote out of staging", outcome.message)
            return
        log.info("%s", outcome.message)
        self.statusBar().showMessage(outcome.message.replace("\n", "   -   "), 8000)
        self.refresh()

    def _on_copy(self, formatted: bool) -> None:
        """Put whatever the page in front is showing on the clipboard.

        Dispatched on the current page rather than sent straight to the viewer.
        It used to go there unconditionally, which was right while every tab but
        one held no copyable document; with an Actions tab, Ctrl+Shift+C on it
        would have copied a transcript somebody was not looking at.

        **This window is the one place either key is bound.** The viewer's own
        two actions — on its context menu and its corner button — carry the key
        as a label and not as a shortcut, because two claims on one shortcut in
        one window is a shortcut Qt fires neither half of.

        The viewer says its own sentence through `copied`; this only speaks for
        the Actions page, which has no signal and one document.
        """
        if self.pages.currentWidget() is self.actions_page:
            if self.actions_page.copy_shown(formatted=formatted):
                flavour = "formatted text" if formatted else "Markdown"
                self.statusBar().showMessage(f"Copied the action items as {flavour}.", 4000)
            return
        self.viewer.copy_formatted() if formatted else self.viewer.copy_markdown()

    # --- What the slow things are doing --------------------------------------

    def on_progress(self, jobs: list[Any]) -> None:
        """Render the running jobs. **On the GUI thread only** — the shell marshals.

        Nothing is shown when nothing is running, rather than an idle bar: a
        progress widget that is always there stops being read.
        """
        if not jobs:
            self.activity_page.on_progress(jobs)
            # Cleared as well as hidden, or the next job flashes the last one's
            # phase for the frame between `show` and the first `setText`.
            self.activity.clear()
            self.activity.hide()
            self.activity_bar.hide()
            return
        # The page gets the whole list; the strip gets the headline. One
        # listener feeds both, because a second would be a second thing to
        # marshal off the reporting thread.
        self.activity_page.on_progress(jobs)
        # The headline is something that is actually *running*, falling back to
        # the oldest. A strip that led with a queued job while a transcription
        # was underway would report the one thing that is not happening.
        job = next((j for j in jobs if j.phase != "queued"), jobs[0])
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

    def open_meeting(self, meeting_id: str, at: float = -1.0) -> None:
        """Show one meeting on the Meetings tab, from wherever it was clicked.

        The filters are cleared first: a meeting reached by following a link from
        somewhere else must not be hidden by a search somebody typed ten minutes
        ago, which would look exactly like a link that did nothing.

        `at` is the second to scroll the transcript to, for an action item that
        cited one. **Negative means do not scroll**, and that distinction earns
        its place: :meth:`referat.ui.viewer.Viewer.goto` brings the *transcript*
        tab forward, so calling it unconditionally would drag every link into the
        evidence when the viewer deliberately opens on the notes. It has to run
        after `refresh()`, which is what fills the viewer, and after `_show`'s own
        `moveCursor` to the top.
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
        if at >= 0:
            self.viewer.goto(at)

    def open_action(self, key: str) -> None:
        """Show one action item on the Actions tab, from the dashboard's box.

        The filters are cleared for the reason :meth:`open_meeting` clears its
        own: a row followed from somewhere else landing behind a stale search box
        looks exactly like a link that did nothing. The person picker is moved to
        whoever owns the item rather than left alone, since the box shows the
        owner's items and the picker may be sitting on somebody else.
        """
        item = next((i for i in self._actions["items"] if i["key"] == key), None)
        self.pages.setCurrentWidget(self.actions_page)
        self.actions_page.clear_filters()
        if item is not None and item["owners"]:
            self.actions_page.select_person(item["owners"][0])
        self.actions_page.set_document(self._actions)

    def open_person_actions(self, name: str) -> None:
        """Show one person's action items, from their page."""
        self.pages.setCurrentWidget(self.actions_page)
        self.actions_page.clear_filters()
        self.actions_page.select_person(name)

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
        self._on_pause_icon(state)
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

        Either argument also brings the **Meetings** tab forward, which phase 6
        made necessary: the window now opens on the dashboard, and a toast asking
        which project a meeting belongs to that opened a summary of every other
        meeting would be a link that did nothing. With neither argument the tab
        is left alone, so the tray menu's plain *Open* still lands wherever the
        window was last.
        """
        if meeting_id is not None or untagged:
            self.pages.setCurrentWidget(self.meetings_page)
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
