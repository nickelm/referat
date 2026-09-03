"""The people page: who Referat knows, and everywhere they turn up.

Phase 5 of build step 20, and the reason this step built a window at all. A
person unifies three things that live in three files — a voiceprint identity in
`.voices/`, the meetings whose `speaker_names` call somebody that, and the
projects those meetings carry — and there was nowhere in three hundred pixels to
put them side by side. This is that page, and clicking a name in a transcript or
in a note's `[[Wikilink]]` is what opens it.

**It shows names, counts and meeting ids, and nothing else.** No embedding
reaches this module, no path into `.voices/` does either, and neither is
available to be shown by accident: :func:`referat.cli.people_document` does not
carry them. The database is biometric personal data about people who never asked
to be in it, and a page *about* those people is the easiest place in this
codebase to leak one.

**The one thing it writes is a deletion**, through
:func:`referat.label.forget_person` and never through
:func:`referat.label.forget` underneath it — the refusal of a name nothing is
filed under and the dashboard regeneration are what make forgetting *correct*,
and a page holding its own copy of those would be the second implementation this
whole arrangement exists to prevent. That is the same sentence
:mod:`referat.ui.tags` says about `cli.apply_tags`, :mod:`referat.ui.speakers`
about `label.name_speaker` and :mod:`referat.ui.projects` about the five project
functions. This module opens no file.

**A name with no voiceprint behind it is shown rather than hidden.** A transcript
that still calls somebody `Anna` while the database holds nothing under that name
is drift — a `rerun` that renumbered past her, or a hand-edited `speaker_names` —
and nothing else on this machine compares those two files. It gets its own
section, the way the projects page gives orphaned tag ids one, and for the same
reason: something quietly disagreeing across three meetings is how you stop being
able to trust any of it.
"""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from referat import cli, label
from referat.config import Config

log = logging.getLogger(__name__)

NAME_ROLE = Qt.ItemDataRole.UserRole
"""The name a row stands for, so nothing parses the rendered cell back."""

TARGET_ROLE = Qt.ItemDataRole.UserRole + 1
"""The meeting id or project id a row navigates to when it is activated."""

PRIVACY = (
    "Voiceprints are biometric data about people who never asked to be recorded. "
    "They never leave this machine, are not synced or backed up, and the /cleanup "
    "pass is denied every read of them. Nothing on this page is an embedding."
)

NOTHING_SELECTED = "Pick somebody."

UNKNOWN = (
    "Nothing is filed under {name}.\n\n"
    "No voiceprint carries that name and no meeting's speaker_names uses it. If it "
    "came from a note, the note spells somebody's name differently from the way "
    "`referat label` filed them - which is worth knowing, and is the reason this "
    "says so rather than selecting nobody."
)

DRIFTED_HEADING = "No voiceprint on file"
DRIFTED_NOTE = (
    "A transcript still calls somebody this and the known-voices database holds "
    "nothing under the name. A rerun that renumbered past them, or a hand edit. "
    "Naming them again in that meeting files a print; `referat label --forget` "
    "takes the name out of the transcripts as well."
)

FORGET = (
    "Delete {name} and revert every label?\n\n"
    "{prints} voiceprint(s) are deleted outright. In {meetings} transcript(s) the "
    "name goes back to the SPEAKER_NN it replaced, and this meeting's snippets were "
    "deleted when the name was first applied - so recognising the voice again means "
    "listening to whatever audio is left, if any.\n\n"
    "Deleting a person is one-way. It is also the whole point of this page being "
    "honest about what is stored."
)


class PeoplePage(QWidget):
    """Everybody Referat knows a name for, one at a time, and where they appear."""

    meeting_requested = Signal(str)
    """A meeting on this page was clicked. The window shows it on the Meetings tab."""

    project_requested = Signal(str)
    """A project on this page was clicked. The window shows it on the Projects tab."""

    def __init__(self, config: Config, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self._document: dict[str, Any] = {"owner": "", "projects": {}, "people": []}
        self._selected: str | None = None
        self._loading = False
        """Set while the list is being rebuilt, so reselecting is not a click."""

        self.people = QListWidget()
        self.people.currentItemChanged.connect(self._on_row_changed)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search people")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._fill_list)

        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.addWidget(self.search)
        left.addWidget(self.people, 1)
        left_pane = QWidget()
        left_pane.setLayout(left)

        # --- The one person being shown --------------------------------------

        self.heading = QLabel()
        self.heading.setTextFormat(Qt.TextFormat.PlainText)
        self.subheading = QLabel()
        self.subheading.setTextFormat(Qt.TextFormat.PlainText)
        self.subheading.setWordWrap(True)

        self.forget_button = QPushButton("Forget this person...")
        self.forget_button.setAutoDefault(False)
        self.forget_button.clicked.connect(self._on_forget)

        title_row = QHBoxLayout()
        title_row.addWidget(self.heading, 1)
        title_row.addWidget(self.forget_button)

        self.prints = QTreeWidget()
        self.prints.setColumnCount(3)
        self.prints.setHeaderLabels(["Filed from", "Cluster", "Added"])
        self.prints.setRootIsDecorated(False)
        self.prints.setUniformRowHeights(True)
        self.prints.setAlternatingRowColors(True)
        self.prints.itemActivated.connect(self._on_print_activated)

        self.appearances = QListWidget()
        self.appearances.itemActivated.connect(self._on_appearance_activated)

        self.projects = QListWidget()
        self.projects.itemActivated.connect(self._on_project_activated)
        self.projects.setMaximumHeight(110)

        privacy = QLabel(PRIVACY)
        privacy.setWordWrap(True)

        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.hide()

        detail = QVBoxLayout()
        detail.addLayout(title_row)
        detail.addWidget(self.subheading)
        detail.addWidget(QLabel("Voiceprints"))
        detail.addWidget(self.prints, 2)
        detail.addWidget(QLabel("Appears in"))
        detail.addWidget(self.appearances, 3)
        detail.addWidget(QLabel("Projects"))
        detail.addWidget(self.projects)
        detail.addWidget(privacy)
        detail.addWidget(self.message)
        self.detail = QWidget()
        self.detail.setLayout(detail)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(left_pane)
        split.addWidget(self.detail)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 5)
        split.setSizes([300, 880])

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(split)
        self.setLayout(layout)
        self._fill_detail(None)

    # --- Reading ------------------------------------------------------------

    def refresh(self) -> None:
        """Re-read the directory, keeping whoever was selected.

        Every meeting root is scanned to build it, which is why the window only
        refreshes the page in front — see
        :meth:`referat.ui.window.CommandCenter.refresh`.
        """
        try:
            self._document = cli.people_document(self.config)
        except Exception:
            log.exception("could not read the people")
            self._complain("Could not read the people - see the log.")
            return
        self._clear_message()
        self._fill_list()

    def _person(self, name: str | None) -> dict[str, Any] | None:
        if name is None:
            return None
        return next((p for p in self._document["people"] if p["name"] == name), None)

    def _fill_list(self) -> None:
        """Build the list through the search box, then reselect what was selected.

        The drifted names get a section of their own at the end. Filtering hides
        rows rather than rebuilding a model, exactly as the tag picker does — the
        document is the truth and this is a view of it.
        """
        needle = self.search.text().strip().casefold()
        wanted = self._selected
        self._loading = True
        try:
            self.people.clear()
            chosen: QListWidgetItem | None = None
            drifted = []
            for person in self._document["people"]:
                if needle and needle not in person["name"].casefold():
                    continue
                if not person["in_database"]:
                    drifted.append(person)
                    continue
                item = self._row(person)
                self.people.addItem(item)
                if person["name"] == wanted:
                    chosen = item
            if drifted:
                self.people.addItem(_heading(DRIFTED_HEADING))
                for person in drifted:
                    item = self._row(person)
                    self.people.addItem(item)
                    if person["name"] == wanted:
                        chosen = item
        finally:
            self._loading = False

        if chosen is not None:
            # Inside the guard, because the detail is filled unconditionally
            # below: reselecting the row that is already current fires no change
            # at all, so a refresh with the same person selected would otherwise
            # keep showing the counts from before it.
            self._loading = True
            try:
                self.people.setCurrentItem(chosen)
            finally:
                self._loading = False
        # Three cases and one call. Nobody selected draws the empty pane; a name
        # the search box is currently hiding keeps its detail, so clearing the box
        # does not also lose the selection; and a name handed over by a link that
        # nobody answers to gets said out loud rather than silently reset to the
        # first row.
        self._fill_detail(self._selected)

    def _row(self, person: dict[str, Any]) -> QListWidgetItem:
        """One name, with the two counts under it that say what it is made of."""
        prints = person["prints"]
        appears = len(person["appears_in"])
        if person["in_database"]:
            under = f"{prints} voiceprint{'s' if prints != 1 else ''}, {appears} meeting"
        else:
            under = f"no voiceprint, {appears} meeting"
        item = QListWidgetItem(
            f"{person['name']}{'  (you)' if person['is_owner'] else ''}\n"
            f"{under}{'s' if appears != 1 else ''}"
        )
        item.setData(NAME_ROLE, person["name"])
        return item

    def _on_row_changed(self, item: QListWidgetItem | None, _previous: object) -> None:
        if self._loading:
            return
        name = str(item.data(NAME_ROLE)) if item is not None and item.data(NAME_ROLE) else None
        self._selected = name
        self._fill_detail(name)

    def _fill_detail(self, name: str | None) -> None:
        """Fill the right-hand pane from the document. Never from the widgets."""
        self._clear_message()
        self.prints.clear()
        self.appearances.clear()
        self.projects.clear()

        person = self._person(name)
        if person is None:
            self.forget_button.setEnabled(False)
            self.heading.setText(name or NOTHING_SELECTED)
            # A name the window was handed by a link and nobody answers to. The
            # honest answer, which is also information about whatever said it.
            self.subheading.setText(UNKNOWN.format(name=name) if name else "")
            return

        self.heading.setText(person["name"] + ("  (you)" if person["is_owner"] else ""))
        self.subheading.setText(self._summary(person))
        # Nothing to delete from the database, and `forget_person` would refuse it
        # in those words anyway. Disabled rather than hidden, as everywhere else
        # here: a button that moves is a button you have to look for.
        self.forget_button.setEnabled(person["in_database"])

        for filing in person["filed_from"]:
            item = QTreeWidgetItem(
                [filing["meeting"], filing["speaker"], filing["added"] or "-"]
            )
            item.setData(0, TARGET_ROLE, filing["meeting"])
            self.prints.addTopLevelItem(item)
        for column in range(3):
            self.prints.resizeColumnToContents(column)

        for meeting_id in person["appears_in"]:
            item = QListWidgetItem(meeting_id)
            item.setData(TARGET_ROLE, meeting_id)
            self.appearances.addItem(item)

        known = self._document["projects"]
        for pid in person["tags"]:
            item = QListWidgetItem(known.get(pid, f"{pid}?"))
            item.setData(TARGET_ROLE, pid)
            self.projects.addItem(item)

    def _summary(self, person: dict[str, Any]) -> str:
        """The one sentence that says why the two lists below differ.

        The gap between them is the join's whole point and is otherwise read as a
        discrepancy: a print records where it was *filed*, and somebody recognised
        automatically files nothing new, so appearing in more meetings than you
        have prints from is the normal state.
        """
        filed = len({f["meeting"] for f in person["filed_from"]})
        appears = len(person["appears_in"])
        if not person["in_database"]:
            return DRIFTED_NOTE
        return (
            f"{person['prints']} voiceprint(s) filed from {filed} meeting(s); the name "
            f"appears in {appears}. Recognition files nothing new, so the second is "
            f"usually the larger."
        )

    # --- Going somewhere ----------------------------------------------------

    def select(self, name: str) -> None:
        """Show this person, or say that nobody is filed under the name.

        Called by the window when a label or a `[[Wikilink]]` is clicked. It
        refreshes first, because the click may be the first thing that has
        happened on this tab and the document behind it may never have been read.
        """
        self._selected = name.strip()
        # Cleared, or a name hidden by a leftover filter would look like a name
        # nobody is filed under — which is the one message on this page that must
        # never be shown wrongly.
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.refresh()

    def _on_print_activated(self, item: QTreeWidgetItem, _column: int) -> None:
        self.meeting_requested.emit(str(item.data(0, TARGET_ROLE)))

    def _on_appearance_activated(self, item: QListWidgetItem) -> None:
        self.meeting_requested.emit(str(item.data(TARGET_ROLE)))

    def _on_project_activated(self, item: QListWidgetItem) -> None:
        pid = str(item.data(TARGET_ROLE))
        # An orphan resolves to no project and the projects page has nothing to
        # select. Rendered here with its `?` because a tag disappearing quietly is
        # how you lose track of a meeting, and left un-navigable for the same
        # reason the projects page leaves it unselectable: it comes off a meeting.
        if pid in self._document["projects"]:
            self.project_requested.emit(pid)

    # --- The one write ------------------------------------------------------

    def _on_forget(self) -> None:
        """Delete a person, behind a modal that says what goes with them.

        Shown *before* the command runs, so there is no outcome to quote yet —
        the same exception the projects page's Delete modal and the sidebar's are,
        and for the same reason. What it says is the part somebody would otherwise
        assume the other way: the labels revert too, everywhere, and the snippets
        that would let you recognise the voice again are already gone.
        """
        person = self._person(self._selected)
        if person is None:
            return
        answer = QMessageBox.question(
            self,
            "Forget this person",
            FORGET.format(
                name=person["name"],
                prints=person["prints"],
                meetings=len(person["appears_in"]),
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        forgotten, message = label.forget_person(self.config, person["name"])
        if not forgotten:
            self._complain(message)
            return
        log.info("%s", message)
        self._selected = None
        self.refresh()

    # --- Messages -----------------------------------------------------------

    def _complain(self, message: str) -> None:
        """Show a refusal in the words of whoever owns the rule, unedited."""
        self.message.setText(message)
        self.message.show()

    def _clear_message(self) -> None:
        self.message.clear()
        self.message.hide()


def _heading(text: str) -> QListWidgetItem:
    """A row that is shown and cannot be selected: the drifted-names section."""
    item = QListWidgetItem(text)
    item.setFlags(Qt.ItemFlag.NoItemFlags)
    return item
