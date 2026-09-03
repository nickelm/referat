"""The opening screen: what just happened, and what is waiting for you.

Phase 6 of build step 20, and the last one that does not need Google. The window
had four tabs and opened on the meetings list, which is an inventory — every
meeting there has ever been, newest at the top, with no distinction between the
ones that are finished and the ones that still owe somebody twenty seconds of
work. This is the other question, and it is the one somebody actually opens the
window to ask: *is there anything for me to do*.

**It writes nothing and reads nothing.** Not one file, and not even a `cli`
function of its own: the window hands it the very `list_document` it has already
read for the meetings tab, so an extra tab costs no extra scan of both meeting
roots — which is the whole reason the window refreshes only the page in front,
and the reason this page is exempt from that rule rather than an exception to it.
The three queues come from :func:`referat.cli.pending`, so *what counts as
pending* is decided once, in Python, beside the listing it is derived from.

**Every row goes somewhere and nothing acts in place.** A queue row opens that
meeting on the Meetings tab, where `Tags…`, `Speakers…` and `Generate notes…`
already are. Draining a queue from here would mean a second path to each of those
three writes — and the tag picker and the labeling dialog are exactly where the
rules about what a tag and a name may be are enforced. A dashboard that wrote
would be a dashboard reimplementing them.

**Themes and action items are deliberately not here.** They were written down as
a later box inside this phase and not its baseline, and the line they may not
cross is stated in `TODO.md`: extraction reads `notes.md`, which is derived, so
it does not breach *nothing is inferred from a transcript* — but it may never tag
a meeting, propose a tag, or reorder the untagged queue by a guess. The human's
tags stay an input everywhere they appear, and the queue below stays in the order
the meetings happened in. The room under the recent list is where they will go,
which is why that list is capped rather than allowed to grow into it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from referat import cli
from referat.ui import icons
from referat.ui.rows import status_text, tags_text

TARGET_ROLE = Qt.ItemDataRole.UserRole
"""The meeting id a row opens, so nothing parses a rendered cell back."""

RECENT = 8
"""How many meetings the *Recent* list holds.

A glance and not an inventory — the Meetings tab is the inventory, and it is one
click away. The heading says how many of how many, because a truncated list that
does not admit to being truncated is a list somebody will one day trust to be
complete.
"""

RECENT_COLUMNS = ("Meeting", "Title", "Duration", "Status", "Projects")

HEADING_ICON = 14
"""How big a queue heading's glyph is drawn.

Sized to the text beside it rather than to a toolbar: this is a label with a
picture in front of it, and a picture taller than its heading reads as a button
that cannot be pressed."""

EMPTY = "No meetings yet. Press Record, or use the hotkey."

NOTHING = "Nothing waiting."
"""What an empty queue shows: a disabled row, so the three keep their places.

The same reason the recorder's buttons are disabled rather than hidden — a thing
that moves is a thing you have to look for.
"""


def _plural(count: int, thing: str) -> str:
    return f"{count} {thing}{'' if count == 1 else 's'}"


def _dot(meeting: dict[str, Any]) -> QIcon:
    """This meeting's lifecycle as a colour, hollowed by a run that lost a channel.

    The same call the meetings list makes, so a row means the same thing on both
    tabs — and the same rule the tray icon has, which is where the ring comes
    from.
    """
    return icons.status_dot(meeting["status"], bool(meeting["missing_channels"]))


def _untagged_detail(meeting: dict[str, Any]) -> str:
    return f"{meeting['duration']} - {status_text(meeting)}"


def _unnamed_detail(meeting: dict[str, Any]) -> str:
    return f"{_plural(len(meeting['unnamed']), 'voice')} nobody has named"


def _notes_detail(meeting: dict[str, Any]) -> str:
    return f"{meeting['duration']} transcribed, no notes yet"


QUEUES = (
    (
        "untagged",
        "Untagged",
        "tag",
        "Meetings carrying no project. Open one and use Tags... - the flow rule is "
        "tag first, then label, so the gallery of names is already narrowed by the "
        "time somebody is offered one.",
        _untagged_detail,
        "Every meeting carries a project.",
    ),
    (
        "unnamed",
        "Speakers nobody has named",
        "person",
        "Meetings with a SPEAKER_NN still in them. Open one and use Speakers... - "
        "naming a voice files a voiceprint, so every later meeting knows it.",
        _unnamed_detail,
        "Every voice has a name.",
    ),
    (
        "notes",
        "No notes yet",
        "page",
        "Meetings with a transcript and no notes.md. Open one and use Generate "
        "notes..., or Notes for all... to queue the whole backlog.",
        _notes_detail,
        "Every meeting has notes.",
    ),
)
"""The three queues, in the order the work is done in. See :func:`referat.cli.pending`.

The tuple carries the key, the heading, the glyph, what the queue *means*, how
one of its rows reads, and what an empty one says. Kept together because a queue
whose heading and whose rows were declared in two places is a queue that will one
day count one thing and describe another.

The glyph is the **same one as the button that drains the queue** — a tag for
`Tags…`, a person for `Speakers…`, a page for `Generate notes…` — and that is the
whole of what it is for. A row here goes to the Meetings tab, where the thing to
press next carries the picture that was on the heading you came from.
"""


class DashboardPage(QWidget):
    """Recent meetings on the left, the three queues on the right."""

    meeting_requested = Signal(str)
    """A row was activated. The window opens that meeting on the Meetings tab."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._document: dict[str, Any] = {"meetings": [], "projects": {}}

        self.summary = QLabel()
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.setWordWrap(True)

        self.recent_heading = QLabel()
        self.recent = QTreeWidget()
        self.recent.setColumnCount(len(RECENT_COLUMNS))
        self.recent.setHeaderLabels(list(RECENT_COLUMNS))
        self.recent.setRootIsDecorated(False)
        self.recent.setAlternatingRowColors(True)
        self.recent.setUniformRowHeights(True)
        self.recent.itemActivated.connect(self._on_recent_activated)
        header = self.recent.header()
        header.setStretchLastSection(False)
        for column in range(len(RECENT_COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.addWidget(self.recent_heading)
        left.addWidget(self.recent, 1)
        left_pane = QWidget()
        left_pane.setLayout(left)

        # One heading and one list per queue, built from the table above rather
        # than written out three times: the alternative is three near-identical
        # blocks that drift the first time one of them gains a column.
        #
        # In a splitter, because the three start out sharing the height equally
        # and the work does not: a morning of untagged meetings against two
        # cleared queues is the normal shape of this page, and dragging is how
        # somebody says so. Equal thirds is only the default — sizing each list
        # to its own contents would make the whole column jump every time a
        # meeting was tagged, which is the thing that makes a queue hard to work.
        ink = self.palette().windowText().color().name()
        self._headings: dict[str, QLabel] = {}
        self._lists: dict[str, QListWidget] = {}
        queues = QSplitter(Qt.Orientation.Vertical)
        for key, title, glyph, why, _detail, _empty in QUEUES:
            heading = QLabel(title)
            heading.setToolTip(why)
            # The glyph goes in a label of its own beside the heading rather than
            # into the text: a QLabel has no icon, and the alternative — rich text
            # with an embedded image — would make a heading that says a count into
            # a string somebody has to escape a meeting title into one day.
            mark = QLabel()
            mark.setPixmap(icons.glyph(glyph, ink).pixmap(HEADING_ICON, HEADING_ICON))
            mark.setToolTip(why)
            widget = QListWidget()
            widget.setToolTip(why)
            widget.setAlternatingRowColors(True)
            widget.itemActivated.connect(self._on_queue_activated)
            self._headings[key] = heading
            self._lists[key] = widget

            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)
            row.addWidget(mark)
            row.addWidget(heading, 1)
            section = QVBoxLayout()
            section.setContentsMargins(0, 0, 0, 0)
            section.addLayout(row)
            section.addWidget(widget, 1)
            holder = QWidget()
            holder.setLayout(section)
            queues.addWidget(holder)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.addWidget(QLabel("Waiting for you"))
        right.addWidget(queues, 1)
        right_pane = QWidget()
        right_pane.setLayout(right)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(left_pane)
        split.addWidget(right_pane)
        split.setStretchFactor(0, 5)
        split.setStretchFactor(1, 3)
        split.setSizes([700, 460])

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.summary)
        layout.addWidget(split, 1)
        self.setLayout(layout)
        self.set_document(self._document)

    # --- Being fed ----------------------------------------------------------

    def set_document(self, document: dict[str, Any]) -> None:
        """Redraw from the listing the window is already holding.

        Not `refresh()`, and the difference is the point: every other page reads
        something when it is switched to, and is refreshed only while in front
        because each read costs a scan of both meeting roots. This one is handed
        the scan the window has already paid for, so it is kept current
        unconditionally and there is nothing to defer.
        """
        self._document = document
        self._fill_summary()
        self._fill_recent()
        queues = cli.pending(document)
        for key, _title, _glyph, _why, detail, empty in QUEUES:
            self._fill_queue(key, queues[key], detail, empty)

    def _fill_summary(self) -> None:
        """Where the meetings are and how many, which nothing else in the window says."""
        meetings = self._document["meetings"]
        if not meetings:
            self.summary.setText(EMPTY)
            return
        staged = sum(1 for m in meetings if m["staged"])
        where = self._document.get("meetings_dir", "")
        text = f"{_plural(len(meetings), 'meeting')} in {where}"
        if staged:
            # Staging is where the audio still is, and the two reasons for that
            # want opposite advice - a rerun, or a promote --release-audio. The
            # count is the honest thing to say from here; `referat list`'s footer
            # is where the advice is, because it can tell the two apart.
            text += f"; {staged} still in staging with audio on disk"
        self.summary.setText(text + ".")

    def _fill_recent(self) -> None:
        """The newest handful, most recent first.

        Reversed here rather than sorted there: `list_document` keeps `referat
        list`'s oldest-first order, which is a property of a console — the newest
        meeting lands next to the prompt — and every window has reversed it
        since phase 1.
        """
        known = self._document["projects"]
        meetings = list(reversed(self._document["meetings"]))
        self.recent.clear()
        for meeting in meetings[:RECENT]:
            item = QTreeWidgetItem(
                [
                    meeting["id"],
                    "" if meeting["title"] == meeting["id"] else meeting["title"],
                    meeting["duration"],
                    status_text(meeting),
                    tags_text(meeting["tags"], known),
                ]
            )
            item.setData(0, TARGET_ROLE, meeting["id"])
            item.setTextAlignment(2, Qt.AlignmentFlag.AlignRight)
            item.setIcon(3, _dot(meeting))
            self.recent.addTopLevelItem(item)
        for column in range(len(RECENT_COLUMNS)):
            self.recent.resizeColumnToContents(column)

        shown = min(len(meetings), RECENT)
        if len(meetings) > shown:
            self.recent_heading.setText(f"Recent meetings ({shown} of {len(meetings)})")
        else:
            self.recent_heading.setText("Recent meetings")

    def _fill_queue(
        self,
        key: str,
        meetings: list[dict[str, Any]],
        detail: Callable[[dict[str, Any]], str],
        empty: str,
    ) -> None:
        """One queue: the count in its heading, newest first, empty said out loud."""
        widget = self._lists[key]
        widget.clear()
        title = next(t for k, t, *_rest in QUEUES if k == key)
        self._headings[key].setText(f"{title} ({len(meetings)})" if meetings else title)
        if not meetings:
            # Shown and unselectable, the way the projects page renders an orphan
            # and the people page a section heading.
            row = QListWidgetItem(f"{NOTHING}  {empty}")
            row.setFlags(Qt.ItemFlag.NoItemFlags)
            widget.addItem(row)
            return
        for meeting in reversed(meetings):
            row = QListWidgetItem(f"{meeting['id']}\n{detail(meeting)}")
            row.setData(TARGET_ROLE, meeting["id"])
            # The lifecycle dot and not the queue's own glyph: the heading
            # already says which queue this is, and the same picture repeated
            # down twelve rows is the one thing on the page carrying no
            # information at all.
            row.setIcon(_dot(meeting))
            widget.addItem(row)

    # --- Going somewhere ----------------------------------------------------

    def _on_queue_activated(self, item: QListWidgetItem) -> None:
        self._go(item.data(TARGET_ROLE))

    def _on_recent_activated(self, item: QTreeWidgetItem, _column: int) -> None:
        self._go(item.data(0, TARGET_ROLE))

    def _go(self, target: Any) -> None:
        """Ask the window for a meeting, if the row stands for one.

        An empty queue's *Nothing waiting* row carries no target and is disabled
        besides, so this is belt and braces — but the two are not the same
        belt: `NoItemFlags` stops a mouse and says nothing about a row reached
        some other way.
        """
        if target:
            self.meeting_requested.emit(str(target))
