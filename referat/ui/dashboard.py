"""The opening screen: what just happened, and what is waiting for you.

Phase 6 of build step 20, and the last one that does not need Google. The window
had four tabs and opened on the meetings list, which is an inventory — every
meeting there has ever been, newest at the top, with no distinction between the
ones that are finished and the ones that still owe somebody twenty seconds of
work. This is the other question, and it is the one somebody actually opens the
window to ask: *is there anything for me to do*.

**It writes nothing and reads nothing.** Not one file, and not even a `cli`
function of its own: the window hands it the very `list_document` it has already
read for the meetings tab — and, since the action items arrived, the
`actions_document` built from that same listing — so an extra tab costs no extra
scan of both meeting roots — which is the whole reason the window refreshes only the page in front,
and the reason this page is exempt from that rule rather than an exception to it.
The three queues come from :func:`referat.cli.pending`, so *what counts as
pending* is decided once, in Python, beside the listing it is derived from.

**Every row goes somewhere and nothing acts in place.** A queue row opens that
meeting on the Meetings tab, where `Tags…`, `Speakers…` and `Generate notes…`
already are. Draining a queue from here would mean a second path to each of those
three writes — and the tag picker and the labeling dialog are exactly where the
rules about what a tag and a name may be are enforced. A dashboard that wrote
would be a dashboard reimplementing them.

**Action items are here now; themes still are not.** They were written down as a
later box inside this phase rather than its baseline, and the room under the
recent list — which is why that list is capped at :data:`RECENT` rather than
allowed to grow into it — is where the box went. The line it may not cross is
still the one `TODO.md` states: extraction reads `notes.md`, which is derived, so
it does not breach *nothing is inferred from a transcript* — but it may never tag
a meeting, propose a tag, or reorder the untagged queue by a guess. Nothing here
does. The human's tags stay an input everywhere they appear, and the queues below
stay in the order the meetings happened in.

**The box is the owner's items and nobody else's.** The Actions tab is where a
person is chosen; this is the opening screen, and the question it answers is
*what do I owe*. Its rows go to that tab rather than to the meeting, which is the
same rule the three queues follow — a row goes where the buttons that act on it
are, and Done, Edit and Drop are there.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from referat import cli
from referat.ui import icons, lists, viewer
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

MINE = 6
"""How many of the owner's action items the box holds.

Smaller than :data:`RECENT`, and for the same reason it exists at all: this is
a glance at what is owed, and the Actions tab one click away is the list. Six
is what fits under the recent meetings without pushing them off a short window.
"""

NO_OWNER = (
    "Set [speakers].owner_name in config.toml, and this box knows which items "
    "are yours."
)
"""What the box says with no owner configured.

An empty name is not *no items*, it is *no way to tell whose they are*, and
the two must not draw the same picture. Every other surface that leans on the
owner's name says so the same way rather than rendering an empty list.
"""

SUMMARIZE = "Summarize..."


def _long_date(when: dt.date) -> str:
    """`Thursday, September 3, 2026`.

    Written out rather than interpolated with `%d`, which pads to `03`, and
    rather than `%-d`, which is a glibc extension the Windows C runtime spells
    `%#d`. Assembling it is shorter than either and depends on neither.
    """
    return f"{when:%A}, {when:%B} {when.day}, {when.year}"


def _day_label(day: str) -> str:
    """`2026-09-03` as `Today`, `Yesterday`, or its own weekday and date.

    Spelled out in full — `Thursday, September 3, 2026` — because this is a
    label somebody reads rather than an id somebody types. The ISO form is the
    file's name, the picker's `currentData` and what `/standup` is handed; it
    stays those and stops being the thing on screen.
    """
    today = dt.date.today()
    try:
        when = dt.date.fromisoformat(day)
    except ValueError:
        return day
    if when == today:
        return f"Today - {_long_date(when)}"
    if when == today - dt.timedelta(days=1):
        return f"Yesterday - {_long_date(when)}"
    return _long_date(when)


BULLET_RE = re.compile(r"^(\s*[-*] +)(.+)$")
"""One bullet of a day summary, and the leader is kept so it can be put back.

Indentation is allowed for the reason the `richtext` parser refuses it: this one
is finding where a bullet's *text* starts, and a wrapped continuation line is not
a bullet at all, so a leading `- ` after whitespace is the only thing that can be
one.
"""


def _meeting_label(meeting_id: str) -> str:
    """`2026-09-03_1408` as `14:08`.

    The date is dropped because the whole page is one date, and what is left is
    the one thing that tells two of that day's meetings apart. A `_2` collision
    suffix is dropped with it — two meetings that started in the same minute read
    as the same time, which is exactly what they are.
    """
    clock = meeting_id.split("_")[1]
    return f"{clock[:2]}:{clock[2:]}"


def day_markdown(text: str, document: dict[str, Any]) -> str:
    """A day summary as the pane renders it: projects in front, links throughout.

    Three rewrites over what `/standup` wrote, and the interesting one is the
    first. **The project on a bullet is Referat's to say and never the prompt's**:
    a meeting's `tags` live in `meta.json`, which `/standup` is forbidden to read,
    and a project inferred from what a meeting sounded like is precisely the guess
    this whole codebase refuses. So the prompt cites the *meeting* — the one thing
    it knows for certain, since it read that file — and the tag the human put on
    that meeting is joined to it here, out of the listing this page was handed.

    Then the citation becomes a link to the meeting, and every `[[Wikilink]]`
    becomes a link to that person, both through :mod:`referat.ui.viewer` so this
    pane and the notes pane cannot come to spell a link two ways.

    **A summary written before the prompt asked for citations still renders**, and
    it renders as what it is: no prefix and no link, because there is nothing on
    the line saying which meeting it came from. Pressing *Rebuild…* is what gives
    it one.
    """
    known = {meeting["id"]: meeting for meeting in document.get("meetings", ())}
    names = document.get("projects", {})
    lines = text.splitlines()
    out: list[str] = []
    for index, line in enumerate(lines):
        bullet = BULLET_RE.match(line)
        if bullet is None:
            out.append(line)
            continue
        cited = viewer.MEETING_ID_RE.findall("\n".join(_bullet_block(lines, index)))
        prefix = _project_prefix([one for one in cited if one in known], known, names)
        out.append(f"{bullet.group(1)}{prefix}{bullet.group(2)}")
    labels = {meeting_id: _meeting_label(meeting_id) for meeting_id in known}
    return viewer.link_people(viewer.link_meetings("\n".join(out), labels))


def _bullet_block(lines: list[str], start: int) -> list[str]:
    """One bullet and the wrapped lines under it, which is where a citation may be.

    The notes wrap a long bullet onto following indented lines and so does a day
    summary, so a citation is not reliably on the line the bullet starts. Reading
    the block rather than the line is what keeps a wrapped bullet from losing its
    project.
    """
    block = [lines[start]]
    for line in lines[start + 1 :]:
        if not line.strip() or BULLET_RE.match(line):
            break
        block.append(line)
    return block


def _project_prefix(
    cited: list[str], known: dict[str, Any], names: dict[str, str]
) -> str:
    """`**Teaching** - ` for the projects the cited meetings carry, or nothing.

    Deduplicated across the citations in first-seen order, and rendered through
    :func:`referat.ui.rows.tags_text` so an orphaned id gets the same trailing
    `?` here as it does in every other list. An untagged meeting gets no prefix
    rather than a placeholder: the untagged queue two boxes down is where that is
    said, and saying it again on every bullet of the day would be noise on the
    one page that exists to be read in fifteen seconds.
    """
    tags: list[str] = []
    for meeting_id in cited:
        for tag in known[meeting_id].get("tags", ()):
            if tag not in tags:
                tags.append(tag)
    if not tags:
        return ""
    return f"**{tags_text(tags, names)}** - "


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
    # A row with notes on disk is here because the transcript was regenerated
    # after they were written -- see `cli.pending` -- and the row says so.
    if meeting.get("notes"):
        return f"{meeting['duration']} re-transcribed, notes older than the transcript"
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
        "Meetings with a transcript and no notes.md, or whose transcript was "
        "re-transcribed after the notes were written. Open one and use Generate "
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

    summary_requested = Signal(str)
    """The summary button was pressed for this date. The window queues a `/standup`."""

    external_requested = Signal(QUrl)
    """A link in the day summary that is neither a name nor a meeting.

    Emitted rather than opened, because handing a URL to the desktop is the one
    thing this window does outside its own process and it happens in exactly one
    place.
    """

    person_requested = Signal(str)
    """A name in the day summary was clicked. The window opens that person.

    The same signal the viewer and the actions page carry, connected to the same
    slot: a `[[Wikilink]]` means one thing wherever it is rendered, and a name
    that opened a page in the notes and did nothing here would read as a broken
    link rather than as a different kind of pane.
    """

    action_requested = Signal(str)
    """An action item was activated. The window opens it on the Actions tab.

    Not the Meetings tab, and the difference is the dashboard's own rule: a row
    goes where the buttons that act on it live. Done, Edit and Drop are on the
    Actions tab; the meeting the item came from is one further hop, from there.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._document: dict[str, Any] = {"meetings": [], "projects": {}}
        self._actions: dict[str, Any] = {"owner": "", "items": [], "counts": {}}
        self._days: dict[str, str] = {}
        """`{date: summary text}` for the days the picker offers, or "" for none.

        Handed over by the window, which reads the files: this page still reads
        nothing itself.
        """

        self.summary = QLabel()
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.setWordWrap(True)

        self.recent_heading = QLabel()
        self.recent = QTreeWidget()
        self.recent.setColumnCount(len(RECENT_COLUMNS))
        self.recent.setHeaderLabels(list(RECENT_COLUMNS))
        self.recent.setRootIsDecorated(False)
        lists.stripe(self.recent)
        self.recent.setUniformRowHeights(True)
        self.recent.itemActivated.connect(self._on_recent_activated)
        header = self.recent.header()
        header.setStretchLastSection(False)
        for column in range(len(RECENT_COLUMNS)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

        # The action items, under the recent list, which is the room `TODO.md`
        # reserved for them and the reason that list is capped. A list rather
        # than a tree: five columns of provenance is what the Actions tab is for,
        # and the question here is only which things are owed.
        ink = self.palette().windowText().color().name()
        self.mine_heading = QLabel()
        mine_mark = QLabel()
        mine_mark.setPixmap(icons.glyph("check", ink).pixmap(HEADING_ICON, HEADING_ICON))
        self.mine = QListWidget()
        lists.stripe(self.mine)
        self.mine.itemActivated.connect(self._on_action_activated)
        mine_row = QHBoxLayout()
        mine_row.setContentsMargins(0, 0, 0, 0)
        mine_row.setSpacing(6)
        mine_row.addWidget(mine_mark)
        mine_row.addWidget(self.mine_heading, 1)

        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.addWidget(self.recent_heading)
        left.addWidget(self.recent, 3)
        left.addLayout(mine_row)
        left.addWidget(self.mine, 2)
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
            lists.stripe(widget)
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

        # The day summary, over the queues. A glance at what has happened, above
        # a glance at what is owed — which is the order somebody reads them in.
        self.day_heading = QLabel()
        day_mark = QLabel()
        day_mark.setPixmap(icons.glyph("calendar", ink).pixmap(HEADING_ICON, HEADING_ICON))
        self.day_pick = QComboBox()
        self.day_pick.currentIndexChanged.connect(self._fill_day)
        self.day_button = QPushButton()
        self.day_button.clicked.connect(self._on_summarize)
        day_row = QHBoxLayout()
        day_row.setContentsMargins(0, 0, 0, 0)
        day_row.setSpacing(6)
        day_row.addWidget(day_mark)
        day_row.addWidget(self.day_heading)
        day_row.addWidget(self.day_pick)
        day_row.addStretch(1)
        day_row.addWidget(self.day_button)

        # A browser rather than a label: the summary is Markdown with bullets and
        # `[[Wikilinks]]` in it, and `setMarkdown` renders exactly the subset the
        # prompt is held to.
        #
        # `setOpenLinks(False)` because every link here is answered in this
        # process - a name opens the People tab, a meeting citation opens the
        # Meetings tab - and a QTextBrowser left to itself would try to *load* an
        # unknown scheme into the pane and blank it. `setOpenExternalLinks(False)`
        # for the other half of the same rule: an `http://` a summary happens to
        # carry is not handed to the desktop without the window saying so.
        self.day_text = QTextBrowser()
        self.day_text.setOpenLinks(False)
        self.day_text.setOpenExternalLinks(False)
        self.day_text.anchorClicked.connect(self._on_day_anchor)
        self.day_text.setMinimumHeight(120)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.addLayout(day_row)
        right.addWidget(self.day_text, 2)
        right.addWidget(QLabel("Waiting for you"))
        right.addWidget(queues, 3)
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
        self.set_actions(self._actions)
        self.set_days({})

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
        # The day summary too, and not only because a bullet's project prefix is
        # read out of this listing: a meeting tagged a minute ago has to stop
        # rendering as untagged without waiting for the day file to be rewritten.
        self._fill_day()
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

    def set_actions(self, document: dict[str, Any]) -> None:
        """Redraw the owner's action items from the document the window holds.

        A second fed document, not a second read — see :meth:`set_document`. The
        window builds this one from the very listing it built the first from, so
        the box costs a redraw and no I/O of its own.
        """
        self._actions = document
        self._fill_mine()

    def _fill_mine(self) -> None:
        """The owner's open items, soonest deadline first, capped at :data:`MINE`.

        `cli.select_actions` decides which they are and what order they come in,
        so this box and the Actions tab cannot come to disagree about either — the
        same reason the three queues below are `cli.pending`'s and not their own.
        """
        owner = self._actions.get("owner", "")
        complaint = self._actions.get("complaint", "")
        self.mine.clear()

        if complaint:
            # An unreadable actions.json reads as *nothing has been done*, which
            # would make this box longer at exactly the moment it is least
            # trustworthy. Say so instead of drawing the longer list.
            row = QListWidgetItem(complaint)
            row.setFlags(Qt.ItemFlag.NoItemFlags)
            self.mine.addItem(row)
            self.mine_heading.setText("My action items")
            return

        items = cli.select_actions(self._actions, owner) if owner else []
        self.mine_heading.setText(
            f"My action items ({len(items)})" if items else "My action items"
        )
        if not items:
            row = QListWidgetItem(
                f"{NOTHING}  {'Nothing is assigned to you.' if owner else NO_OWNER}"
            )
            row.setFlags(Qt.ItemFlag.NoItemFlags)
            self.mine.addItem(row)
            return

        for item in items[:MINE]:
            due = f"due {item['due']}" if item["due"] else "no date"
            row = QListWidgetItem(f"{item['text']}\n{due} - {item['meeting']}")
            row.setData(TARGET_ROLE, item["key"])
            self.mine.addItem(row)
        if len(items) > MINE:
            # Said out loud, for the reason the recent list says how many of how
            # many: a truncated list that does not admit to being truncated is one
            # somebody will eventually trust to be complete.
            row = QListWidgetItem(f"... and {len(items) - MINE} more on the Actions tab")
            row.setFlags(Qt.ItemFlag.NoItemFlags)
            self.mine.addItem(row)

    def set_days(self, days: dict[str, str]) -> None:
        """Redraw the day summaries from the text the window has already read.

        Today and yesterday, which are the two the summary is for: what has
        happened so far, and what happened before that. Both are offered even
        when neither has been written, because the button that writes one has to
        be reachable from somewhere.
        """
        self._days = days
        wanted = self.day_pick.currentData()
        self.day_pick.blockSignals(True)
        self.day_pick.clear()
        for day in sorted(days, reverse=True):
            self.day_pick.addItem(_day_label(day), day)
        index = self.day_pick.findData(wanted)
        self.day_pick.setCurrentIndex(max(0, index))
        self.day_pick.blockSignals(False)
        self._fill_day()

    def _fill_day(self) -> None:
        day = self.day_pick.currentData()
        self.day_heading.setText("The day")
        if not day:
            self.day_text.setMarkdown("")
            self.day_button.setEnabled(False)
            return
        text = self._days.get(day, "")
        self.day_text.setMarkdown(
            day_markdown(text, self._document)
            if text
            else f"*No summary for {_day_label(day)} yet. {SUMMARIZE} reads that "
            f"day's notes and writes a short one.*"
        )
        # "Rebuild" rather than "Summarize" once one exists, because pressing it
        # again is the documented way to fold in a meeting that has since been
        # written up - and a button that still said "Summarize" would look like
        # it had not worked the first time.
        self.day_button.setText("Rebuild..." if text else SUMMARIZE)
        self.day_button.setEnabled(True)

    def _on_day_anchor(self, url: QUrl) -> None:
        """A link in the day summary: a name, a meeting, or somebody else's problem.

        The same three-way dispatch :meth:`referat.ui.viewer.Viewer._on_anchor`
        makes, narrowest first, and it goes through the same parsers so the two
        panes cannot come to disagree about what a `referat-person:` is. There is
        no timestamp arm: a day summary spans several meetings and a bare
        `[HH:MM:SS]` in one would not say which transcript to scroll.

        An unrecognised scheme is handed to the window rather than dropped, the
        way the viewer hands one over: `setOpenExternalLinks(False)` means Qt
        will not open it, a summary may carry a real URL, and the one place that
        opens anything outside this process stays
        :meth:`referat.ui.window.CommandCenter._on_external_link`.
        """
        name = viewer.parse_person(url)
        if name is not None:
            self.person_requested.emit(name)
            return
        meeting_id = viewer.parse_meeting(url)
        if meeting_id is not None:
            self.meeting_requested.emit(meeting_id)
            return
        self.external_requested.emit(url)

    def _on_summarize(self) -> None:
        day = self.day_pick.currentData()
        if day:
            self.summary_requested.emit(str(day))

    # --- Going somewhere ----------------------------------------------------

    def _on_queue_activated(self, item: QListWidgetItem) -> None:
        self._go(item.data(TARGET_ROLE))

    def _on_action_activated(self, item: QListWidgetItem) -> None:
        target = item.data(TARGET_ROLE)
        if target:
            self.action_requested.emit(str(target))

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
