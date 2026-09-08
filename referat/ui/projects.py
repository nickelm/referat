"""The projects page: what a thread of work is called, and what it is about.

Phase 4 of build step 20, and the third thing the command center writes. It
writes projects and only projects, through :func:`referat.cli.create_project`,
`rename_project`, `set_description`, `set_glossary` and `remove_project` — never
:class:`referat.projects.ProjectsDB`'s own methods, which are primitives that
change one field of an in-memory object and save nothing. The unreadable-file
guard, the name rules, the unknown-id refusal and the single save around them are
what make a change *correct*, and a page reaching past them would be the second
implementation the one-implementation rule exists to prevent. That is the same
sentence :mod:`referat.ui.tags` says about `cli.apply_tags` and the same one
:mod:`referat.ui.speakers` says about `label.name_speaker`. This module opens no
file.

**Editing a glossary is hotword management, and that is why it is here.** A
project's `glossary` is already the place a term of art lives, read twice at two
different times: every glossary is merged into the one global list handed to
faster-whisper, which acts *before* any meeting has been tagged, and the
glossaries of a meeting's tags are the second list `/cleanup` normalizes its
notes against. A separate screen about hotwords would be a screen about a file
rather than about the work. So the merged list sits underneath this page as a
**read-only panel**, showing every term with the source it came from and — the
part that earns it — what the 223-token cap dropped. A cap nobody can see is how
this turns into a bug report about one specific name that is never heard right,
and this is the page somebody will be standing on when they add the term that
pushes the list over.

**`[transcription].hotword_extras` is shown and never edited.**
:mod:`referat.config` promises that Referat parses `config.toml` and never writes
it back, so hand edits and comments survive; a page that wrote one key of it
would either break that promise or make `config.toml` a second place to say what
`projects.json` says. Extras are by definition the terms belonging to no project
and no person, which is a short and rarely-touched list — the cost of a text
editor is low and the cost of the promise is not.

**Linking a Google Doc is here since phase 7**, which is what made this the last
phase of the command center: step 13 built the digests and left them reachable
only from a prompt, and this was already the page where a project's documents
belong. *Link doc...* opens :mod:`referat.ui.docs`, which takes a pasted share
link and shows the document's tabs; *Unlink* and *Sync now* sit beside it. All
three drive `cli.link_doc`, `cli.unlink_doc` and `cli.sync_project` and none of
them implements a rule — a refusal about a missing tab arrives here listing the
document's actual tabs because that is the sentence `cli` wrote.

**The two that reach the network run on a thread**, and not one line of Qt runs
on it: the result crosses back through the `doc_job_done` signal and the phases
go through :mod:`referat.progress`. This process owns the recorder, and a hung
request on the GUI thread is a window somebody cannot stop a meeting from — the
same reasoning that put the notes queue on a worker.

**This page cannot authenticate and does not try.** Google consent needs a
terminal by construction, so a machine that has never consented gets the sentence
saying to run one command at a prompt, once. That is a property of the paste flow
`referat.gdocs` chose rather than a limitation this page ran into.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from referat import cli, gdocs, progress
from referat.config import Config
from referat.projects import clean_terms
from referat.ui import docs as docs_dialog
from referat.ui import icons, lists, viewer

log = logging.getLogger(__name__)

ID_ROLE = Qt.ItemDataRole.UserRole
"""The project id a row stands for, so nothing parses a display name back."""

TOGGLE_ROLE = Qt.ItemDataRole.UserRole + 1
"""Set on the foldable Archived heading, so a click is recognised by role."""

ARCHIVED_ROLE = Qt.ItemDataRole.UserRole + 2
"""Set on the rows the Archived heading folds, so folding hides them in place.

Carried on the row rather than recomputed from the document, so the toggle is a
walk of the list and never a rebuild — the tag picker's rule, and for its reason:
rebuilding re-runs the reselect and the dirty check to hide four rows.
"""

GLOSSARY_HELP = (
    "One term per line: the names, products and jargon of this thread of work. "
    "Every glossary on this machine is merged into the one hotword list Whisper is "
    "told about before it transcribes anything, and the glossaries of a meeting's "
    "tags are what /cleanup normalizes its notes against."
)

DOCS_UNLINKED = "No Google Doc yet. Link one to push this project's notes into it."

EXTRAS_NOTE = (
    "Terms from `config` are [transcription].hotword_extras, hand-edited in "
    "{path}. Referat parses config.toml and never writes it back, so that list is "
    "read here and edited there."
)

NOTHING_SELECTED = "Pick a project, or create one."

ARCHIVED_HEADING = "Archived"
ARCHIVED_NOTE = (
    "Finished threads of work, hidden from the tag picker and nowhere else. Every "
    "meeting keeps its tag and it still resolves to this name, the glossary still "
    "feeds the hotword list, and `referat tag` will still add it. Select one to "
    "edit or unarchive it."
)

ARCHIVE_CONFIRM = (
    "Archive {name}?\n\n"
    "Its {meetings} keep the tag and it still resolves to that name. Its glossary "
    "still feeds the hotword list Whisper is handed, and `referat tag` will still "
    "add it.\n\n"
    "What changes is that the tag picker stops offering it and it moves to the "
    "Archived section here. Nothing is deleted, and Unarchive puts it back."
)

ORPHAN_HEADING = "Orphaned tags"
ORPHAN_NOTE = (
    "Ids meetings still carry that no project answers to, left behind by a deleted "
    "project. Nothing here edits them: an orphan comes off a meeting, on the "
    "Meetings tab, and never off a project that no longer exists."
)

DISCARD = "Discard the unsaved changes to {name}?"

RECAP = "Recap..."
RECAP_NONE = (
    "*No recap yet. Recap... reads the notes of every meeting tagged with this "
    "project and writes a short brief: where it stands, and what needs discussing. "
    "Read it minutes before the meeting.*"
)
RECAP_NO_NOTES = "*None of this project's meetings has notes yet, so there is nothing to recap.*"
RECAP_UNTAGGED = "*No meeting is tagged with this project yet.*"
RECAP_CURRENT = "Current - written {generated} from {meetings}."
RECAP_STALE = "Stale - written {generated} from {meetings}. {reason}"
RECAP_QUEUED = "Queued - the activity tab shows the pass."
"""What the line beside *Recap...* says. Stale is a fact derived in Python on
every read and shown here, never acted on: the recap stays, and the button
beside it is how it is rewritten."""


def _recap_label(meeting: dict[str, Any]) -> str:
    """`2026-09-03T11:01:13` as `2026-09-03 11:01`, for a citation's link text.

    The date is kept, unlike the day summary's labels, because a recap spans
    weeks and the date is the one thing that says how old an open item is; the
    time is kept because two meetings of one project can fall on one day.
    """
    started = meeting.get("started_at", "")
    return f"{started[:10]} {started[11:16]}".strip() or meeting["id"]


def recap_markdown(document: dict[str, Any]) -> str:
    """A recap as the pane renders it: citations and names as links.

    The same two rewrites :func:`referat.ui.dashboard.day_markdown` makes,
    through the same functions in :mod:`referat.ui.viewer`, so a citation means
    the same thing on both pages. No project prefix, since every bullet here is
    about the one project the page is showing. The guest list for
    :func:`referat.ui.viewer.link_meetings` is the recap's own `series`, so a
    citation of a meeting outside it stays plain text — which is what a
    citation the prompt should not have written looks like.
    """
    labels = {meeting["id"]: _recap_label(meeting) for meeting in document.get("series", ())}
    return viewer.link_people(viewer.link_meetings(document.get("body", ""), labels))


class ProjectsPage(QWidget):
    """Every project, one editable at a time, over the merged hotword list."""

    doc_job_done = Signal(bool, str)
    """`(ok, message)` from a link or a sync worker.

    A queued signal, because the thread that produced it may not touch a widget.
    The same crossing `ui.shell.Bridge` makes for transitions and notifications,
    and for the same reason: `Shell.notify` once ran a whole refresh on a
    transcription thread and corrupted the heap three times.
    """

    recap_requested = Signal(str)
    """A project id whose recap should be written. The window queues it.

    Not run on this page's own doc-job thread, deliberately: a recap is a
    `claude` pass, and every `claude` pass on this machine goes through the
    window's one-worker queue — one rate limit, one folder. This page only asks.
    """

    meeting_requested = Signal(str)
    """A meeting id cited in the recap: `[2026-09-03_1101]` was clicked."""

    person_requested = Signal(str)
    """A `[[Wikilink]]` in the recap: a name was clicked."""

    external_requested = Signal(QUrl)
    """A link in the recap this page does not answer, for the window to open."""

    def __init__(self, config: Config, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self._document: dict[str, Any] = {"projects": [], "orphans": {}, "complaint": ""}
        self._selected: str | None = None
        self._loading = False
        """Set while the form is being filled, so filling it does not read as editing."""

        self._syncing = False
        """Set while a link or a sync is in flight, so a second cannot be started.

        Two syncs of one project at once would each hold indices from their own
        `documents.get`, and the second would apply them to a document the first
        had already moved.
        """

        self.doc_job_done.connect(self._on_doc_job_done)

        self._archived_open = False
        """Whether the Archived section is unfolded. Folded on open, deliberately.

        A finished project is the one somebody is *least* likely to have come here
        for, and the count in the heading is enough to say it is still there. Page
        state and not stored anywhere: it survives a refresh because this object
        does, and resets when the window does. Deliberately not persisted: a
        folded section is a glance somebody took, not a preference they set.
        """

        self.projects = QListWidget()
        self.projects.currentItemChanged.connect(self._on_row_changed)
        self.projects.itemClicked.connect(self._on_row_clicked)

        # Read once and kept, rather than per row: `icons.glyph` caches on the
        # colour anyway, and this page rebuilds its whole list on every refresh.
        self._tag = icons.glyph("tag", self.palette().windowText().color().name())
        self._archive = icons.glyph("archive", self.palette().windowText().color().name())

        self.new_field = QLineEdit()
        self.new_field.setPlaceholderText("New project")
        self.new_field.returnPressed.connect(self._on_create)
        self.create_button = QPushButton("Create")
        self.create_button.setAutoDefault(False)
        self.create_button.clicked.connect(self._on_create)

        creating = QHBoxLayout()
        creating.setContentsMargins(0, 0, 0, 0)
        creating.addWidget(self.new_field, 1)
        creating.addWidget(self.create_button)

        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.addWidget(self.projects, 1)
        left.addLayout(creating)
        left_pane = QWidget()
        left_pane.setLayout(left)

        # --- The one project being edited -----------------------------------

        self.heading = QLabel()
        self.heading.setTextFormat(Qt.TextFormat.PlainText)
        self.subheading = QLabel()
        self.subheading.setTextFormat(Qt.TextFormat.PlainText)
        self.subheading.setWordWrap(True)

        self.rename_button = QPushButton("Rename...")
        self.rename_button.setAutoDefault(False)
        self.rename_button.clicked.connect(self._on_rename)
        self.archive_button = QPushButton("Archive...")
        # `windowText` and not a state colour. `icons.LIFECYCLE` is keyed on
        # `meta.json`'s `status` and is about *meetings*, so borrowing its grey
        # for a project would be exactly the drift that palette guards against by
        # keying on the rendered string. Archiving is not a lifecycle state.
        self.archive_button.setIcon(
            icons.glyph("archive", self.palette().windowText().color().name())
        )
        self.archive_button.setAutoDefault(False)
        self.archive_button.clicked.connect(self._on_archive)
        self.delete_button = QPushButton("Delete...")
        # The same red trash the meetings page's Delete carries: two different
        # destructive buttons on two tabs, one picture, so neither has to be
        # recognised on its own.
        self.delete_button.setIcon(icons.glyph("trash", icons.LIFECYCLE["failed"]))
        self.delete_button.setAutoDefault(False)
        self.delete_button.clicked.connect(self._on_delete)

        self.description = QLineEdit()
        self.description.setPlaceholderText("One line about this thread of work")
        self.description.textChanged.connect(self._on_edited)

        self.glossary = QPlainTextEdit()
        self.glossary.setPlaceholderText("One term per line")
        self.glossary.textChanged.connect(self._on_edited)

        glossary_help = QLabel(GLOSSARY_HELP)
        glossary_help.setWordWrap(True)

        # A list rather than the label this was until phase 7, because a doc is
        # now something you select in order to unlink it. It stays short by
        # nature -- a project has one or two -- so it is sized to its contents
        # rather than given a share of the pane.
        self.docs = QListWidget()
        self.docs.setMaximumHeight(88)
        lists.stripe(self.docs)
        self.docs.currentItemChanged.connect(lambda *_: self._refresh_doc_buttons())
        self.docs.itemDoubleClicked.connect(self._on_open_doc)

        self.link_button = QPushButton("Link doc...")
        self.link_button.setAutoDefault(False)
        self.link_button.clicked.connect(self._on_link_doc)
        self.auto_sync = QCheckBox("Sync automatically when notes are written")
        self.auto_sync.setToolTip(
            "On by default. Turn it off for a document other people read and you "
            "want to look over first -- Sync now still works while it is off."
        )
        self.auto_sync.toggled.connect(self._on_auto_sync)

        self.open_doc_button = QPushButton("Open in browser")
        self.open_doc_button.setAutoDefault(False)
        self.open_doc_button.clicked.connect(
            lambda: self._on_open_doc(self.docs.currentItem())
        )
        self.unlink_button = QPushButton("Unlink")
        self.unlink_button.setAutoDefault(False)
        self.unlink_button.clicked.connect(self._on_unlink_doc)
        self.sync_button = QPushButton("Sync now")
        self.sync_button.setAutoDefault(False)
        self.sync_button.clicked.connect(self._on_sync)

        self.save_button = QPushButton("Save")
        self.save_button.setAutoDefault(False)
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self._on_save)
        self.revert_button = QPushButton("Revert")
        self.revert_button.setAutoDefault(False)
        self.revert_button.setEnabled(False)
        self.revert_button.clicked.connect(lambda: self._fill_form(self._selected))

        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.hide()

        # Harmless, then reversible, then destructive and last — so the red one
        # stays at the end of the row where it has always been.
        title_row = QHBoxLayout()
        title_row.addWidget(self.heading, 1)
        title_row.addWidget(self.rename_button)
        title_row.addWidget(self.archive_button)
        title_row.addWidget(self.delete_button)

        save_row = QHBoxLayout()
        save_row.addStretch(1)
        save_row.addWidget(self.revert_button)
        save_row.addWidget(self.save_button)

        detail = QVBoxLayout()
        detail.addLayout(title_row)
        detail.addWidget(self.subheading)
        detail.addWidget(QLabel("Description"))
        detail.addWidget(self.description)
        detail.addWidget(QLabel("Glossary"))
        detail.addWidget(glossary_help)
        detail.addWidget(self.glossary, 1)
        # The same order the title row uses and for the same reason: harmless,
        # then reversible, then the one that goes out to the network and writes
        # into somebody's document.
        docs_row = QHBoxLayout()
        docs_row.setContentsMargins(0, 0, 0, 0)
        docs_row.addStretch(1)
        docs_row.addWidget(self.open_doc_button)
        docs_row.addWidget(self.link_button)
        docs_row.addWidget(self.unlink_button)
        docs_row.addWidget(self.sync_button)

        detail.addWidget(QLabel("Google Docs"))
        detail.addWidget(self.docs)
        detail.addWidget(self.auto_sync)
        detail.addLayout(docs_row)

        # --- The recap (build step 24) -------------------------------------
        #
        # Under the documents, because it is the other thing a project *is*
        # over time: the docs are where its notes go, and this is what they add
        # up to. The state line is Python's verdict and the button beside it is
        # the one way it changes; the pane renders the file the way the
        # dashboard renders a day, a `[<meeting-id>]` becoming a link.
        self.recap_state = QLabel()
        self.recap_state.setWordWrap(True)
        self.recap_button = QPushButton(RECAP)
        self.recap_button.setAutoDefault(False)
        self.recap_button.clicked.connect(self._on_recap)
        recap_row = QHBoxLayout()
        recap_row.setContentsMargins(0, 0, 0, 0)
        recap_row.addWidget(QLabel("Recap"))
        recap_row.addWidget(self.recap_state, 1)
        recap_row.addWidget(self.recap_button)

        # The same two settings the dashboard's day pane runs under, for the
        # same reason: every link here is answered in this process, and a
        # QTextBrowser left to itself would *load* an unknown scheme into the
        # pane and blank it.
        self.recap_text = QTextBrowser()
        self.recap_text.setOpenLinks(False)
        self.recap_text.setOpenExternalLinks(False)
        self.recap_text.anchorClicked.connect(self._on_recap_anchor)
        self.recap_text.setMinimumHeight(120)

        detail.addLayout(recap_row)
        detail.addWidget(self.recap_text, 1)
        detail.addLayout(save_row)
        detail.addWidget(self.message)
        self.detail = QWidget()
        self.detail.setLayout(detail)

        # --- The merged hotword list ----------------------------------------

        self.hotwords = QTreeWidget()
        self.hotwords.setColumnCount(2)
        self.hotwords.setHeaderLabels(["Hotword", "Source"])
        self.hotwords.setRootIsDecorated(False)
        self.hotwords.setUniformRowHeights(True)
        lists.stripe(self.hotwords)
        self.hotword_summary = QLabel()
        self.hotword_summary.setWordWrap(True)
        self.hotword_note = QLabel()
        self.hotword_note.setWordWrap(True)

        hotwords = QVBoxLayout()
        hotwords.setContentsMargins(0, 6, 0, 0)
        hotwords.addWidget(QLabel("What Whisper is told before it transcribes"))
        hotwords.addWidget(self.hotwords, 1)
        hotwords.addWidget(self.hotword_summary)
        hotwords.addWidget(self.hotword_note)
        hotword_pane = QWidget()
        hotword_pane.setLayout(hotwords)

        top = QSplitter(Qt.Orientation.Horizontal)
        top.addWidget(left_pane)
        top.addWidget(self.detail)
        top.setStretchFactor(0, 2)
        top.setStretchFactor(1, 5)
        top.setSizes([300, 880])

        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(top)
        split.addWidget(hotword_pane)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        # Sized as well as weighted: the stretch factors alone gave the hotword
        # panel three visible rows, and a panel whose whole point is that the cap
        # can be seen has to show enough of the list to see it in.
        split.setSizes([430, 300])

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(split)
        self.setLayout(layout)

    # --- Reading ------------------------------------------------------------

    def refresh(self) -> None:
        """Re-read the projects and the hotword list, keeping the selected project.

        Both documents, every time. The hotword panel exists precisely so that a
        term added to a glossary can be seen landing in the merged list — or
        being dropped by the cap — and a panel that only refreshed when something
        asked it to would be showing the state before the edit.
        """
        try:
            self._document = cli.project_document(self.config)
        except Exception:
            log.exception("could not read the projects")
            self._document = {"projects": [], "orphans": {}, "complaint": ""}
            self._refuse("Could not read the projects - see the log.")
            return
        self._fill_list()
        self._fill_hotwords()
        # The recap alone, not the form: `refresh()` is how the result of a
        # queued pass reaches this page, and refilling the form would discard a
        # half-typed glossary -- the `_refresh_docs` pattern, a fifth time.
        self._refresh_recap()
        if self._document["complaint"]:
            # An unreadable `projects.json` loads as *no projects*, which is the
            # rule that keeps a broken file from costing a transcript and which
            # here would draw an empty page offering to create the first project
            # into a file already full of them. Said, and creation refused.
            self._refuse(self._document["complaint"])
        else:
            self.new_field.setEnabled(True)
            self.create_button.setEnabled(True)
            self._clear_message()

    def _refuse(self, complaint: str) -> None:
        """Show what stopped the page and leave nothing on it that would write."""
        self.new_field.setEnabled(False)
        self.create_button.setEnabled(False)
        self.detail.setEnabled(False)
        self._complain(complaint)

    def _fill_list(self) -> None:
        """Build the live projects, then the archived, then the orphans, and reselect.

        Three sections ordered by how much of a project each thing is: one being
        worked on, one that is finished, and an id that is not a project at all.
        """
        wanted = self._selected
        self._loading = True
        try:
            self.projects.clear()
            chosen: QListWidgetItem | None = None

            def add(project: dict[str, Any], hidden: bool = False) -> None:
                nonlocal chosen
                second = _meetings(project["meetings"])
                if project["archived_at"]:
                    second = f"{second} - archived {project['archived_at'][:10]}"
                item = QListWidgetItem(f"{project['name']}\n{second}")
                item.setData(ID_ROLE, project["id"])
                if project["archived_at"]:
                    item.setData(ARCHIVED_ROLE, True)
                # The tag that is on this page's tab, on its button and on the
                # dashboard's untagged queue. A project *is* a label, and the
                # glyph saying so in four places is the cheapest way to say it.
                # The archived ones carry the archive box instead -- the one list
                # in this UI where a second glyph earns its place, because it is
                # what distinguishes two otherwise identical kinds of row.
                item.setIcon(self._archive if project["archived_at"] else self._tag)
                self.projects.addItem(item)
                item.setHidden(hidden)
                if project["id"] == wanted:
                    chosen = item

            live = [p for p in self._document["projects"] if not p["archived_at"]]
            archived = [p for p in self._document["projects"] if p["archived_at"]]
            for project in live:
                add(project)
            if archived:
                # **Folded unless something needs it open**, and the two overrides
                # are not conveniences. A selected project that has just been
                # archived would otherwise vanish from the list while its form is
                # still on screen; and with no live projects at all, a folded
                # section is a page that looks empty while holding four projects.
                # `_archived_open` is then synced to what was actually drawn, so
                # the next click on the heading closes what is visible rather than
                # toggling a state nobody can see.
                self._archived_open = (
                    self._archived_open
                    or not live
                    or any(p["id"] == wanted for p in archived)
                )
                # A heading like the orphans', and there the resemblance stops
                # twice over: this one folds, and these rows stay **selectable**.
                # Unarchiving one means selecting it first, and everything in the
                # form still edits it -- the glossary above all, which still feeds
                # the hotword list.
                self.projects.addItem(
                    _toggle_heading(ARCHIVED_HEADING, self._archived_open, len(archived))
                )
                for project in archived:
                    add(project, hidden=not self._archived_open)
            orphans = self._document["orphans"]
            if orphans:
                # A section of its own: a tag quietly
                # vanishing off three meetings is how you lose track of what a
                # meeting was about, so a deleted project's ids are shown rather
                # than hidden. Unselectable, because there is no project here to
                # edit -- an orphan comes off a meeting.
                self.projects.addItem(_heading(ORPHAN_HEADING))
                for pid, count in orphans.items():
                    self.projects.addItem(_heading(f"{pid}  ({_meetings(count)})"))
        finally:
            self._loading = False

        if chosen is not None:
            self.projects.setCurrentItem(chosen)
        elif self.projects.count() and self._first_project() is not None:
            self.projects.setCurrentItem(self._first_project())
        else:
            self._selected = None
            self._fill_form(None)
        # Reselecting the row that was already selected fires no change and so
        # refills no form -- which is what keeps a half-typed glossary across a
        # refresh -- and it means the Save button's state has to be recomputed
        # here. Without this it stayed lit after a save, offering to write again
        # what had just been written.
        self._on_edited()

    def _first_project(self) -> QListWidgetItem | None:
        for row in range(self.projects.count()):
            item = self.projects.item(row)
            if item.data(ID_ROLE):
                return item
        return None

    def _project(self, pid: str | None) -> dict[str, Any] | None:
        if pid is None:
            return None
        return next((p for p in self._document["projects"] if p["id"] == pid), None)

    def _fill_hotwords(self) -> None:
        """The merged list, read-only, from :func:`referat.cli.hotwords_document`.

        The same document `referat hotwords --json` prints. Nothing here merges
        anything: which terms survive the cap and in which order is
        :mod:`referat.hotwords`' fixed priority, and a panel that sorted them
        would be showing a list Whisper is not given.
        """
        try:
            document = cli.hotwords_document(self.config)
        except Exception:
            log.exception("could not build the hotword list")
            self.hotword_summary.setText("Could not build the hotword list - see the log.")
            return
        self.hotwords.clear()
        for term in document["terms"]:
            self.hotwords.addTopLevelItem(QTreeWidgetItem([term["term"], term["source"]]))
        for column in (0, 1):
            self.hotwords.resizeColumnToContents(column)

        kept = len(document["terms"])
        summary = (
            f"{kept} term(s), about {document['estimated_tokens']} of "
            f"{document['budget']} tokens of Whisper's prompt window."
        )
        dropped = document["dropped"]
        if dropped:
            # Named rather than counted, for the reason `referat hotwords` names
            # them: a term silently missing is a bug report about one name that
            # is never heard right.
            summary += " Over the budget and dropped, lowest priority first: " + ", ".join(
                f"{t['term']} ({t['source']})" for t in dropped
            )
        self.hotword_summary.setText(summary)
        self.hotword_note.setText(
            EXTRAS_NOTE.format(path=self.config.source or "config.toml")
        )

    # --- The form -----------------------------------------------------------

    def _on_row_clicked(self, item: QListWidgetItem | None) -> None:
        """Fold or unfold the Archived section. The only click a heading answers.

        A walk of the rows rather than a rebuild, which is the tag picker's rule:
        rebuilding would re-run the reselect and the dirty check in order to hide
        four rows, and `_fill_list` would then have to be careful not to lose a
        half-typed glossary. Hiding in place cannot.
        """
        if item is None or not item.data(TOGGLE_ROLE):
            return
        self._archived_open = not self._archived_open
        count = 0
        for row in range(self.projects.count()):
            other = self.projects.item(row)
            if other.data(ARCHIVED_ROLE):
                other.setHidden(not self._archived_open)
                count += 1
        item.setText(_toggle_text(ARCHIVED_HEADING, self._archived_open, count))

    def _on_row_changed(self, item: QListWidgetItem | None, previous: object) -> None:
        """Show another project, offering to keep whatever was typed into this one.

        The only place this page can lose work, so it is the only place it asks.
        Cancelling puts the selection back, which is why the guard flag is needed
        at all: `setCurrentItem` re-enters this handler.
        """
        if self._loading:
            return
        pid = str(item.data(ID_ROLE)) if item is not None and item.data(ID_ROLE) else None
        if pid == self._selected:
            return
        if any(self._dirty()) and not self._may_discard():
            self._loading = True
            try:
                self._select(self._selected)
            finally:
                self._loading = False
            return
        self._selected = pid
        self._fill_form(pid)

    def select_project(self, pid: str) -> None:
        """Show one project, from a link somewhere else in the window.

        Goes through :meth:`refresh` rather than straight to the row, because the
        caller may be arriving on a page that has never been read. Setting
        `_selected` first is what makes the reselect in :meth:`_fill_list` land on
        it, and that is also why the form has to be filled explicitly afterwards:
        reselecting fires no change, which is the very property that keeps a
        half-typed glossary across a refresh.

        The unsaved-changes prompt is asked here for the same reason — arriving by
        a link is a row change and `_on_row_changed` will not see it as one.
        """
        if pid != self._selected and any(self._dirty()) and not self._may_discard():
            return
        self._selected = pid
        self.refresh()
        self._fill_form(pid)

    def _select(self, pid: str | None) -> None:
        for row in range(self.projects.count()):
            item = self.projects.item(row)
            if item.data(ID_ROLE) and str(item.data(ID_ROLE)) == pid:
                self.projects.setCurrentItem(item)
                return

    def _may_discard(self) -> bool:
        project = self._project(self._selected)
        name = project["name"] if project else self._selected
        answer = QMessageBox.question(
            self,
            "Unsaved changes",
            DISCARD.format(name=name),
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Discard

    def _fill_form(self, pid: str | None) -> None:
        """Fill the detail pane from the document. Never from the widgets."""
        project = self._project(pid)
        self._clear_message()
        self._loading = True
        try:
            if project is None:
                self.heading.setText(NOTHING_SELECTED)
                self.subheading.setText(ORPHAN_NOTE if self._document["orphans"] else "")
                self.description.clear()
                self.glossary.setPlainText("")
                self.docs.clear()
                self._refresh_doc_buttons()
                self.recap_state.clear()
                self.recap_text.setMarkdown("")
                self.recap_button.setEnabled(False)
                self.detail.setEnabled(False)
                return
            self.detail.setEnabled(True)
            self.heading.setText(project["name"])
            summary = (
                f"id {project['id']} - {_meetings(project['meetings'])} - the id never "
                f"moves, which is why meetings store it rather than the name"
            )
            self._fill_archive_state(project, summary)
            self.description.setText(project["description"])
            self.glossary.setPlainText("\n".join(project["glossary"]))
            self._fill_docs(project)
            self._fill_recap(project)
        finally:
            self._loading = False
        self._on_edited()

    def _fill_archive_state(self, project: dict[str, Any], summary: str | None = None) -> str:
        """The two parts of the form that say whether this project is archived.

        Split out of :meth:`_fill_form` so that :meth:`_on_archive` can refresh
        *only* these after a write. Refilling the whole form there would silently
        discard a half-typed glossary — archiving does not save one — and leaving
        it alone was worse: the button still read `Archive...` on a project that
        had just been archived, which is the one control somebody would click
        next.
        """
        if summary is None:
            summary = self.subheading.text().split("\n")[0]
        if project["archived_at"]:
            summary = f"{summary}\nArchived {project['archived_at'][:10]}. {ARCHIVED_NOTE}"
        self.subheading.setText(summary)
        # The button says the direction; the section and the subheading say the
        # state. Deliberately **not** disabling the form for an archived project:
        # the glossary is live data whatever the project's state, since it still
        # feeds the hotword list.
        self.archive_button.setText("Unarchive" if project["archived_at"] else "Archive...")
        return summary

    def _typed(self) -> tuple[str, list[str]]:
        """What the form currently says: the description, and the glossary by line.

        Cleaning is deliberately **not** done here — `set_glossary` collapses
        whitespace, drops empties and deduplicates on the way into the file,
        through the same :func:`referat.projects.clean_terms` a hand edit goes
        through. Cleaning in the widget as well would be a second implementation
        of it, and the two would disagree the first time one changed.
        """
        return self.description.text(), self.glossary.toPlainText().splitlines()

    def _dirty(self) -> tuple[bool, bool]:
        """Whether the description and the glossary differ from what was loaded.

        Compared against the document rather than against a snapshot, so a
        refresh that arrives from somewhere else cannot leave this page thinking
        an already-saved edit is still pending. The glossary compares *cleaned*,
        or reordering nothing and pressing Save would write the file.
        """
        project = self._project(self._selected)
        if project is None:
            return False, False
        description, terms = self._typed()
        return (
            " ".join(description.split()) != project["description"],
            clean_terms(terms) != project["glossary"],
        )

    def _on_edited(self) -> None:
        """Light the Save button when anything differs. Silent while loading."""
        if self._loading:
            return
        dirty = any(self._dirty())
        self.save_button.setEnabled(dirty)
        self.revert_button.setEnabled(dirty)

    # --- Writing ------------------------------------------------------------

    def _on_create(self) -> None:
        """Make a project from the typed name and select it.

        The id comes back **from the function that made it** and is never looked
        up by display name: it is `slugify` plus a `-2` collision suffix, and two
        projects are allowed to share a name. The same rule
        :mod:`referat.ui.tags` records, for the same reason.
        """
        name = self.new_field.text().strip()
        if not name:
            return
        project, complaint = cli.create_project(self.config, name)
        if project is None:
            self._complain(complaint)
            return
        self.new_field.clear()
        self._selected = project.id
        self.refresh()

    def _on_rename(self) -> None:
        """Change the display name. The id does not move, and the dialog says so."""
        project = self._project(self._selected)
        if project is None:
            return
        name, ok = QInputDialog.getText(
            self,
            "Rename project",
            f"A new display name for {project['name']}.\nIts id stays {project['id']}, "
            f"so no meeting record is disturbed.",
            text=project["name"],
        )
        if not ok:
            return
        outcome = cli.rename_project(self.config, project["id"], name)
        if not outcome.ok:
            self._complain(outcome.message)
            return
        log.info("%s", outcome.message)
        self.refresh()
        # Reselecting the same row fires no change and refills no form -- the
        # property that keeps a half-typed glossary across a refresh -- so the
        # one thing that just changed is updated directly, as the archive
        # button does for its own state.
        updated = self._project(self._selected)
        if updated is not None:
            self.heading.setText(updated["name"])

    def _on_archive(self) -> None:
        """Archive or unarchive, behind a modal that says what archiving does not do.

        **Only archiving asks.** Unarchiving loses nothing and restores nothing —
        it clears one field — so a confirmation there would be a dialog for the
        sake of symmetry. The archive modal is shown before the command runs and
        so is the same exception the Delete modals are; what it says is the half
        somebody assumes the other way, which is that nothing is hidden *from a
        meeting*.

        `_selected` is deliberately **not** cleared afterwards, unlike
        :meth:`_on_delete`'s: the project still exists, and the reselect finds it
        because archived rows keep their `ID_ROLE`.
        """
        project = self._project(self._selected)
        if project is None:
            return
        archiving = not project["archived_at"]
        if archiving:
            answer = QMessageBox.question(
                self,
                "Archive project",
                ARCHIVE_CONFIRM.format(
                    name=project["name"], meetings=_meetings(project["meetings"])
                ),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        outcome = cli.set_archived(self.config, project["id"], archiving)
        if not outcome.ok:
            self._complain(outcome.message)
            return
        log.info("%s", outcome.message)
        self.refresh()
        # The list is rebuilt by `refresh`, but reselecting the row that was
        # already selected fires no change and so refills no form -- the property
        # that keeps a half-typed glossary across a refresh. The two parts that
        # just became wrong are updated directly rather than by refilling.
        updated = self._project(self._selected)
        if updated is not None:
            self._fill_archive_state(updated)

    def _on_delete(self) -> None:
        """Delete a project, behind a modal that says what it leaves behind.

        The modal is shown *before* the command runs, so there is no outcome to
        quote yet and this is the one place the page says something Python also
        says — the same exception every Delete modal here is. What it says
        is the consequence somebody would otherwise assume the other way: the
        tags stay, on every meeting carrying them, as orphans.
        """
        project = self._project(self._selected)
        if project is None:
            return
        carrying = project["meetings"]
        detail = (
            f"{_meetings(carrying)} still carry its id. They keep carrying it, as an "
            f"orphaned tag, and nothing else is touched - no meeting record, no "
            f"notes, no Google Doc."
            if carrying
            else "No meeting carries its id."
        )
        answer = QMessageBox.question(
            self,
            "Delete project",
            f"Delete {project['name']}?\n\n{detail}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        outcome = cli.remove_project(self.config, project["id"])
        if not outcome.ok:
            self._complain(outcome.message)
            return
        log.info("%s", outcome.message)
        self._selected = None
        self.refresh()

    def _on_save(self) -> None:
        """Write whichever of the two fields changed, and stop at the first refusal.

        Two calls rather than one, because they are two operations with two
        messages and `projects.json` is rewritten whole and atomically by each —
        so the worst an interruption between them can do is leave the description
        saved and the glossary not, which the refresh then shows honestly. A
        composite write would need a third guarded function and a message that
        described both.

        Only the dirty half is written, so pressing Save with one field touched
        does not rewrite the other.
        """
        project = self._project(self._selected)
        if project is None:
            return
        description_dirty, glossary_dirty = self._dirty()
        description, terms = self._typed()
        for dirty, call in (
            (description_dirty, lambda: cli.set_description(self.config, project["id"], description)),
            (glossary_dirty, lambda: cli.set_glossary(self.config, project["id"], terms)),
        ):
            if not dirty:
                continue
            outcome = call()
            if not outcome.ok:
                # The typed text stays, so it can be corrected rather than
                # retyped. A refusal is the one case the form is not refilled.
                self._complain(outcome.message)
                self.refresh()
                return
            log.info("%s", outcome.message)
        self.refresh()
        # Refilled from what the file now holds rather than left as typed:
        # `set_glossary` collapses whitespace, drops blank lines and
        # deduplicates, and a box still showing the three lines that became two
        # would be this page holding an opinion about somebody else's field.
        self._fill_form(self._selected)

    # --- The recap (build step 24) ------------------------------------------

    def _fill_recap(self, project: dict[str, Any]) -> None:
        """Draw one project's recap and its state, from `cli.recap_document`.

        Everything on this line is decided in Python and arrives decided: which
        meetings are in the series, which have notes, whether the file is stale
        and why. The widget holds no rule. It opens no file either — the
        document carries the recap's text — and a document it cannot get is a
        sentence rather than an empty pane.
        """
        try:
            document = cli.recap_document(self.config, project["id"])
        except Exception:
            log.exception("could not read the recap of %s", project["id"])
            self.recap_state.setText("Could not read the recap - see the log.")
            self.recap_text.setMarkdown("")
            self.recap_button.setEnabled(False)
            return
        noted = [m for m in document["series"] if m["notes"]]
        if document["complaint"]:
            self.recap_state.setText(document["complaint"])
            self.recap_text.setMarkdown("")
        elif not document["exists"]:
            self.recap_state.setText("")
            self.recap_text.setMarkdown(
                RECAP_NONE if noted else (RECAP_NO_NOTES if document["series"] else RECAP_UNTAGGED)
            )
        else:
            self.recap_text.setMarkdown(recap_markdown(document))
            words = dict(
                generated=document["generated"][:16].replace("T", " ") or "an unknown time",
                meetings=_meetings(len(document["meetings"])),
            )
            if document["stale"]:
                reasons = document["reasons"]
                more = f" (and {len(reasons) - 1} more)" if len(reasons) > 1 else ""
                self.recap_state.setText(
                    RECAP_STALE.format(**words, reason=f"{reasons[0].capitalize()}{more}.")
                )
                self.recap_state.setToolTip("\n".join(reasons))
            else:
                self.recap_state.setText(RECAP_CURRENT.format(**words))
                self.recap_state.setToolTip("")
        self._refresh_recap_button(
            project["id"],
            exists=document["exists"] and not document["complaint"],
            can_write=bool(noted) and not document["complaint"],
        )

    def _refresh_recap(self) -> None:
        """Redraw the recap alone, from the project just re-read. See :meth:`refresh`."""
        project = self._project(self._selected)
        if project is not None:
            self._fill_recap(project)

    def _refresh_recap_button(self, pid: str, *, exists: bool, can_write: bool) -> None:
        """"Recap..." or "Rebuild...", and disabled while a pass for it is queued.

        "Rebuild" once one exists, for the reason the dashboard's day button
        changes: pressing it again is the documented way to fold in a meeting
        written up since, and a button still saying "Recap" would look like it
        had not worked the first time.

        Whether one is in flight is read off :mod:`referat.progress`, which is
        live state the window's queue already reports into — so this page needs
        no second record of what it asked for, and a pass started from the
        prompt shows here exactly as one started from this button does.
        """
        from referat import notes as notes_module

        key = notes_module.recap_progress_key(pid)
        running = any(job.key == key for job in progress.active())
        self.recap_button.setText("Rebuild..." if exists else RECAP)
        self.recap_button.setEnabled(can_write and not running)
        if running:
            self.recap_state.setText(RECAP_QUEUED)

    def _on_recap(self) -> None:
        """Ask the window to queue a `/recap` for the selected project.

        Said on the state line at once, so the click visibly did something; the
        button re-enables when the pass is off the queue and the window
        refreshes this page.
        """
        project = self._project(self._selected)
        if project is None:
            return
        self.recap_state.setText(RECAP_QUEUED)
        self.recap_button.setEnabled(False)
        self.recap_requested.emit(project["id"])

    def _on_recap_anchor(self, url: QUrl) -> None:
        """A link in the recap: a name, a meeting, or the window's to open.

        The same three-way dispatch the dashboard's day pane makes, through the
        same parsers, so the two pages cannot disagree about what a
        `referat-meeting:` is.
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

    # --- Messages -----------------------------------------------------------

    # --- Google Docs (build step 20, phase 7) -------------------------------

    def _fill_docs(self, project: dict[str, Any]) -> None:
        """The documents this project is linked to, one row each.

        A row carries the `gdoc_id` rather than showing it, because what somebody
        reads is which document and which tab, and what `unlink_doc` needs is the
        id — the same split every list in this window makes between a display
        name and the id underneath it.
        """
        self.docs.clear()
        for doc in project["docs"]:
            # **The document's title leads, not the tab's.** A row answers
            # "which document is this project written into", and the tab is the
            # detail underneath it -- it was the other way round for one
            # afternoon and every row read as a fragment of a name.
            tab = doc["tab_name"] or doc["tab_id"] or "(no tab recorded)"
            name = doc["doc_title"] or doc["gdoc_id"]
            # No glyph, for the reason `icons.py` already records: a list's rows
            # do not repeat their heading's, because the same picture down every
            # row is the one thing on a page carrying no information. It also
            # cannot be got right -- an icon is painted once in `windowText` and
            # a selected row here is dark blue, so the glyph went black on blue.
            item = QListWidgetItem(f"{name}  ({tab})")
            item.setData(ID_ROLE, doc["gdoc_id"])
            item.setToolTip(
                f"{name}\ntab {tab}  [{doc['tab_id']}]\n{doc['gdoc_id']}\n\n"
                "Double-click to open it in your browser."
            )
            self.docs.addItem(item)
        if not project["docs"]:
            item = QListWidgetItem(DOCS_UNLINKED)
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.docs.addItem(item)
        # Set under the loading flag, or filling the form reads as somebody
        # ticking the box and writes `projects.json` on every refresh.
        was_loading = self._loading
        self._loading = True
        try:
            self.auto_sync.setChecked(bool(project.get("auto_sync", True)))
        finally:
            self._loading = was_loading
        self._refresh_doc_buttons()

    def _refresh_doc_buttons(self) -> None:
        """Unlink needs a selected doc; Sync needs one to exist. Both need a project.

        Disabled rather than hidden, which is the same choice the meetings tab
        makes for *Re-transcribe...* and *Promote...*: a button that is there and
        greyed says the operation exists and what it wants, and a button that
        vanishes says nothing at all.
        """
        project = self._project(self._selected)
        linked = bool(project and project["docs"])
        selected = self.docs.currentItem()
        has_doc = bool(selected and selected.data(ID_ROLE))
        self.auto_sync.setEnabled(project is not None and not self._syncing)
        self.open_doc_button.setEnabled(has_doc)
        self.link_button.setEnabled(project is not None and not self._syncing)
        self.unlink_button.setEnabled(has_doc and not self._syncing)
        self.sync_button.setEnabled(linked and not self._syncing)

    def _on_auto_sync(self, checked: bool) -> None:
        """Write the checkbox through the guarded function, never through `ProjectsDB`.

        Written immediately rather than waiting for *Save*, because it is not
        part of the form: the description and the glossary are text somebody is
        part-way through typing and this is a switch, which is the same reason
        Archive is a button rather than a field.
        """
        if self._loading or self._selected is None:
            return
        self._show_outcome(cli.set_auto_sync(self.config, self._selected, checked))

    def _refresh_docs(self) -> None:
        """Redraw the doc list alone, from the document just re-read.

        **`refresh()` does not refill the form, deliberately**, and that is what
        made linking a document look like it had done nothing: the list beneath
        the button kept the state from before the write until you clicked away
        and back. The reason `refresh()` leaves the form alone is good --
        refilling it would discard a half-typed glossary, which is the same
        reason `_fill_archive_state` exists -- so the fix is the same shape as
        that one: touch the part that changed and nothing else.

        Third time this page has needed it, and the third is the one that says
        it is a pattern rather than a coincidence. `_on_rename`'s stale heading
        is the fourth and is still open in `TODO.md`.
        """
        project = self._project(self._selected)
        if project is not None:
            self._fill_docs(project)

    def _icon_color(self) -> str:
        return self.palette().windowText().color().name()

    def _on_open_doc(self, item: QListWidgetItem | None) -> None:
        """Open the selected document in the browser. The one thing here that leaves Qt.

        `QDesktopServices` rather than `webbrowser`, because this is the UI layer
        and that is the toolkit's own answer -- and because the portability rule
        in :mod:`referat.ui` is about not reaching past Qt when Qt has one.
        Enabled only on a real row: the placeholder saying there is no document
        carries no id, which is the same test *Unlink* uses.
        """
        gdoc_id = item.data(ID_ROLE) if item is not None else None
        if not gdoc_id:
            return
        QDesktopServices.openUrl(QUrl(gdocs.doc_url(gdoc_id)))

    def _on_link_doc(self) -> None:
        """Ask which document and which tab, then link and backfill on a thread."""
        project = self._project(self._selected)
        if project is None:
            return
        dialog = docs_dialog.LinkDocDialog(self.config, project["name"], self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        answers = dialog.result_link()
        pid = project["id"]
        self._run_doc_job(
            f"Linking {pid}",
            progress.SYNC,
            pid,
            lambda: cli.link_doc(self.config, pid, **answers),
        )

    def _on_unlink_doc(self) -> None:
        """Detach one document. Local, instant, and behind a modal that says what it is not.

        A modal rather than a straight click because the button sits beside two
        that reach the network, and because the thing worth saying is what
        unlinking does *not* do — the same shape as the archive modal one section
        up.
        """
        project = self._project(self._selected)
        item = self.docs.currentItem()
        if project is None or item is None or not item.data(ID_ROLE):
            return
        gdoc_id = item.data(ID_ROLE)
        confirmed = QMessageBox.question(
            self,
            "Unlink this document?",
            f"Referat will stop writing into it.\n\n"
            f"The document is not touched: every block it already holds stays exactly "
            f"where it is, and this is not a delete. Each meeting also keeps its record "
            f"of what was written there, so linking the same document again finds those "
            f"blocks already current instead of re-rendering every one of them.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmed != QMessageBox.StandardButton.Yes:
            return
        outcome = cli.unlink_doc(self.config, project["id"], gdoc_id)
        self._show_outcome(outcome)
        self._refresh_docs()
        self._refresh_doc_buttons()

    def _on_sync(self) -> None:
        """Reconcile this project's docs against the meetings tagged with it."""
        project = self._project(self._selected)
        if project is None:
            return
        pid = project["id"]
        self._run_doc_job(
            f"Syncing {pid}",
            progress.SYNC,
            pid,
            lambda: cli.sync_project(
                self.config, pid, progress=lambda phase: progress.step(f"sync:{pid}", phase)
            ),
        )

    def _run_doc_job(self, what: str, kind: str, target: str, work) -> None:
        """Run one network operation on a thread and report it when it lands.

        **On a thread because this process owns the recorder.** A `documents.get`
        plus one `batchUpdate` per block is a network round trip per meeting, and
        a hung request on the GUI thread is a window somebody cannot stop a
        meeting from — the same reasoning that put the notes queue on a worker.

        Not one line of Qt runs in `work`: the result crosses back through
        :attr:`doc_job_done`, which is a queued signal, and the phases go through
        :mod:`referat.progress`, which knows nothing about toolkits. That is the
        rule this project paid three heap corruptions for.

        One at a time, and the buttons say so while it runs. Two syncs of the
        same project at once would each hold indices from their own
        `documents.get` and the second would apply them to a document the first
        had already moved.
        """
        self._syncing = True
        self._refresh_doc_buttons()
        self._complain(f"{what}...")
        progress.begin(f"{kind}:{target}", kind, target, "starting")

        def run() -> None:
            try:
                outcome = work()
            except Exception as exc:  # noqa: BLE001 - a page may not kill the process
                log.exception("%s failed", what)
                self.doc_job_done.emit(False, f"{what} failed: {exc}")
            else:
                self.doc_job_done.emit(outcome.ok, outcome.message)
            finally:
                progress.end(f"{kind}:{target}")

        threading.Thread(target=run, name="digest", daemon=True).start()

    def _on_doc_job_done(self, ok: bool, message: str) -> None:
        """Back on the GUI thread. Refresh, then say what happened."""
        self._syncing = False
        self.refresh()
        self._refresh_docs()
        self._refresh_doc_buttons()
        self._show_outcome(cli.Outcome(ok, message))

    def _show_outcome(self, outcome: cli.Outcome) -> None:
        """An outcome in the words its rule's owner wrote, unedited.

        The guarantee `cli.Outcome` exists for, and the reason a refusal about a
        missing tab arrives here listing the document's actual tabs rather than
        as "could not link".
        """
        if outcome.ok:
            self.refresh()
        self._complain(outcome.message)

    def _complain(self, message: str) -> None:
        """Show a refusal in the words of whoever owns the rule, unedited."""
        self.message.setText(message)
        self.message.show()

    def _clear_message(self) -> None:
        self.message.clear()
        self.message.hide()


def _meetings(count: int) -> str:
    return f"{count} meeting{'s' if count != 1 else ''}"


def _heading(text: str) -> QListWidgetItem:
    """A row that is shown and cannot be selected: the orphan section and its ids."""
    item = QListWidgetItem(text)
    item.setFlags(Qt.ItemFlag.NoItemFlags)
    return item


def _toggle_heading(text: str, open_: bool, count: int) -> QListWidgetItem:
    """A heading that can be clicked to fold its section, and still cannot be selected.

    **Enabled but not selectable**, which is the one flag between this and
    :func:`_heading`. `QListWidget` delivers no `itemClicked` for a row with
    `NoItemFlags` — a disabled row is not clickable — so a foldable heading has
    to be enabled; leaving `ItemIsSelectable` off is what keeps it out of the
    selection, out of arrow-key navigation, and out of `_first_project`'s search,
    exactly as the inert headings are.

    The count is in the text rather than implied by the rows, because a folded
    section whose rows are hidden would otherwise be a heading that says nothing
    about what it is hiding.
    """
    item = QListWidgetItem(_toggle_text(text, open_, count))
    item.setFlags(Qt.ItemFlag.ItemIsEnabled)
    item.setData(TOGGLE_ROLE, True)
    return item


def _toggle_text(text: str, open_: bool, count: int) -> str:
    """A foldable heading's label. One implementation, because two would drift.

    Written once and called from both the build and the fold: the toggle rewrites
    the label in place rather than rebuilding the row, so the two would otherwise
    be two copies of the same format string.
    """
    return f"{'▾' if open_ else '▸'}  {text} ({count})"
