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

**A person is a record since build step 20c**, and this page is keyed by its
id. What a link hands over is still a *name* — a transcript label is the short
name and a `[[Wikilink]]` is whatever the note wrote — so :meth:`PeoplePage.select`
resolves it: an id, then a full name, then a short name, and two people the
text fits equally are both said rather than one picked. The second thing the
page writes is a **rename**, through :func:`referat.label.rename_person`, which
is the operation and holds every rule about what propagates where; the dialog
here is three fields and nothing else.
"""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
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
from referat.ui import icons, lists

log = logging.getLogger(__name__)

ID_ROLE = Qt.ItemDataRole.UserRole
"""The person id a row stands for, so nothing parses the rendered cell back."""

TARGET_ROLE = Qt.ItemDataRole.UserRole + 1
"""The meeting id or project id a row navigates to when it is activated."""

PRIVACY = (
    "Voiceprints are biometric data about people who never asked to be recorded. "
    "They never leave this machine, are not synced or backed up, and the /cleanup "
    "pass is denied every read of them. Nothing on this page is an embedding."
)

NOTHING_SELECTED = "Pick somebody."

AMBIGUOUS = (
    "{name} could be any of: {ids}. Showing the first; pick the other from the list."
)
"""Two people the clicked text fits. Said, never silently decided."""

UNKNOWN = (
    "Nothing is filed under {name}.\n\n"
    "No voiceprint carries that name and no meeting's speaker_names uses it. If it "
    "came from a note, the note spells somebody's name differently from the way "
    "`referat label` filed them - which is worth knowing, and is the reason this "
    "says so rather than selecting nobody."
)

INACTIVE_HEADING = "Only archived projects"
INACTIVE_NOTE = (
    "Every project this name is tagged with has been archived. Derived on every "
    "read and stored nowhere - unarchive one of their projects and they move back "
    "up. Somebody who appears in an untagged meeting is not here: no project is "
    "not a finished one, and neither is an orphaned tag."
)
"""The heading names the fact rather than calling somebody inactive.

The document's field is `inactive`, which is a predicate about tags; a heading
reading *Inactive* over a list of people would be this page holding an opinion
about a person, which is the register its privacy note is careful to stay out of.
"""

DRIFTED_HEADING = "No voiceprint on file"
DRIFTED_NOTE = (
    "A transcript still calls somebody this and the known-voices database holds "
    "nothing under the name. A rerun that renumbered past them, or a hand edit. "
    "Naming them again in that meeting files a print; `referat label --forget` "
    "takes the name out of the transcripts as well."
)

RENAME_NOTE = (
    "The id does not change. A new short name is relabeled into every transcript "
    "this person appears in, and [[Wikilinks]] in those meetings' notes are "
    "rewritten; prose is left alone, and a linked Google Doc holds the old name "
    "until its next sync. The email is stored and never sent."
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

    actions_requested = Signal(str)
    """This person's action items were asked for. The window opens the Actions tab.

    A page rather than a fourth list here, and deliberately: an action item
    belongs to a *meeting*, which this page already lists, and copying them in
    would be a second surface for something the Actions tab exists to be. What
    this adds is the count and the way there.
    """

    def __init__(self, config: Config, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self._document: dict[str, Any] = {
            "owner": "",
            "projects": {},
            "archived": [],
            "people": [],
        }
        self._selected: str | None = None
        self._open_actions: dict[str, int] = {}
        """`{name: open action items}`, handed over by the window.

        Fed rather than read, because this page is one of the two that *do*
        read for themselves and the action items are already built once per
        refresh for the dashboard and the Actions tab. A third read of every
        `notes.md` to put a number on a button would be the thing this
        codebase keeps deleting.
        """
        self._loading = False
        """Set while the list is being rebuilt, so reselecting is not a click."""

        self.people = QListWidget()
        self.people.currentItemChanged.connect(self._on_row_changed)

        # Read once and kept: this page rebuilds every list on every refresh, and
        # `icons.glyph` caches on the colour anyway. The *meeting* and *project*
        # glyphs are here for a reason worth stating — `Appears in` and
        # `Projects` are two lists of bare ids, one under the other, and without
        # them the only thing distinguishing a meeting id from a project name is
        # knowing which heading you are under.
        ink = self.palette().windowText().color().name()
        self._person_icon = icons.glyph("person", ink)
        self._meeting_icon = icons.glyph("list", ink)
        self._project_icon = icons.glyph("tag", ink)

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
        # The same red trash as the other two destructive buttons, and this is
        # the most destructive of the three: deleting a meeting leaves the person,
        # and deleting a project leaves the tags, but this reverts labels in every
        # transcript the name appears in.
        self.forget_button.setIcon(icons.glyph("trash", icons.LIFECYCLE["failed"]))
        self.forget_button.setAutoDefault(False)
        self.forget_button.clicked.connect(self._on_forget)

        # Beside the heading and before the destructive one: this is the thing
        # somebody most often wants from a person's page, and Forget is the thing
        # they most rarely want.
        self.actions_button = QPushButton("Action items")
        self.actions_button.setIcon(icons.glyph("check", self.palette().windowText().color().name()))
        self.actions_button.setAutoDefault(False)
        self.actions_button.clicked.connect(self._on_actions)

        # Between the harmless button and the destructive one, which is the
        # projects page's own order: Rename, Archive, Delete.
        self.rename_button = QPushButton("Rename...")
        self.rename_button.setAutoDefault(False)
        self.rename_button.clicked.connect(self._on_rename)

        title_row = QHBoxLayout()
        title_row.addWidget(self.heading, 1)
        title_row.addWidget(self.actions_button)
        title_row.addWidget(self.rename_button)
        title_row.addWidget(self.forget_button)

        self.prints = QTreeWidget()
        self.prints.setColumnCount(3)
        self.prints.setHeaderLabels(["Filed from", "Cluster", "Added"])
        self.prints.setRootIsDecorated(False)
        self.prints.setUniformRowHeights(True)
        lists.stripe(self.prints)
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

    def _person(self, pid: str | None) -> dict[str, Any] | None:
        if pid is None:
            return None
        return next((p for p in self._document["people"] if p["id"] == pid), None)

    def _matching(self, text: str) -> list[dict[str, Any]]:
        """Everybody `text` could mean: by id, else full name, else short name.

        The same order :meth:`referat.voices.VoicesDB.resolve` uses, applied to
        the document rather than the database because this page may not open
        `voices.json` — and applied to the drifted entries too, whose only
        spelling is the one a transcript still uses.
        """
        wanted = text.strip()
        folded = wanted.casefold()
        people = self._document["people"]
        if exact := [p for p in people if p["id"] == wanted]:
            return exact
        for pick in (
            lambda p: p["name"] == wanted,
            lambda p: p["name"].casefold() == folded,
            lambda p: p["short"] == wanted,
            lambda p: p["short"].casefold() == folded,
        ):
            if hits := [p for p in people if pick(p)]:
                return hits
        return []

    def _fill_list(self) -> None:
        """Build the list through the search box, then reselect what was selected.

        Two sections at the end, and their order is the point: people all of
        whose projects are archived, then the drifted names. **Drift wins where
        somebody is both**, because it is the known-voices database disagreeing
        with a transcript — a thing to fix — while having only archived projects
        is simply what a finished collaboration looks like.

        Filtering hides rows rather than rebuilding a model, exactly as the tag
        picker does — the document is the truth and this is a view of it.
        """
        needle = self.search.text().strip().casefold()
        wanted = self._selected
        self._loading = True
        try:
            self.people.clear()
            chosen: QListWidgetItem | None = None
            inactive = []
            drifted = []

            def place(person: dict[str, Any]) -> None:
                nonlocal chosen
                item = self._row(person)
                self.people.addItem(item)
                if person["id"] == wanted:
                    chosen = item

            for person in self._document["people"]:
                haystack = " ".join(
                    (person["name"], person["short"], person["id"], person.get("email", ""))
                ).casefold()
                if needle and needle not in haystack:
                    continue
                if not person["in_database"]:
                    drifted.append(person)
                elif person["inactive"]:
                    inactive.append(person)
                else:
                    place(person)
            if inactive:
                self.people.addItem(_heading(INACTIVE_HEADING))
                for person in inactive:
                    place(person)
            if drifted:
                self.people.addItem(_heading(DRIFTED_HEADING))
                for person in drifted:
                    place(person)
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
            f"{_shown(person)}{'  (you)' if person['is_owner'] else ''}\n"
            f"{under}{'s' if appears != 1 else ''}"
        )
        item.setData(ID_ROLE, person["id"])
        item.setIcon(self._person_icon)
        return item

    def _on_row_changed(self, item: QListWidgetItem | None, _previous: object) -> None:
        if self._loading:
            return
        pid = str(item.data(ID_ROLE)) if item is not None and item.data(ID_ROLE) else None
        self._selected = pid
        self._fill_detail(pid)

    def set_actions(self, document: dict[str, Any]) -> None:
        """Take the open-item counts per person from the window's action document.

        Counted here rather than read: the window already built this document for
        the dashboard and the Actions tab, so the number costs a walk of a list
        that is already in memory.
        """
        counts: dict[str, int] = {}
        for item in document.get("items", []):
            if item["done"] or item["dismissed"]:
                continue
            for owner in item["owners"]:
                counts[owner] = counts.get(owner, 0) + 1
        self._open_actions = counts
        self._fill_detail(self._selected)

    def _open_for(self, person: dict[str, Any] | None) -> int:
        """Open items owned by either spelling of this person.

        A note writes whatever the transcript called somebody, which is the
        short name, and an older note may carry the full one; the two are
        summed rather than one chosen, since an item is owned by a name and
        both names are theirs.
        """
        if person is None:
            return 0
        spellings = {person["short"], person["name"]}
        return sum(self._open_actions.get(name, 0) for name in spellings)

    def _on_actions(self) -> None:
        # The Actions tab filters by the owner string a note wrote, which is
        # the short name — what the transcript called them.
        if (person := self._person(self._selected)) is not None:
            self.actions_requested.emit(person["short"])

    def _fill_detail(self, name: str | None) -> None:
        """Fill the right-hand pane from the document. Never from the widgets."""
        self._clear_message()
        self.prints.clear()
        self.appearances.clear()
        self.projects.clear()

        person = self._person(name)
        open_items = self._open_for(person)
        self.actions_button.setText(
            f"Action items ({open_items})" if open_items else "Action items"
        )
        # Disabled with nothing open, rather than hidden: the button keeps its
        # place, which is the same rule Forget and the recorder's three follow.
        self.actions_button.setEnabled(bool(open_items))

        if person is None:
            self.forget_button.setEnabled(False)
            self.rename_button.setEnabled(False)
            self.heading.setText(name or NOTHING_SELECTED)
            # A name the window was handed by a link and nobody answers to. The
            # honest answer, which is also information about whatever said it.
            self.subheading.setText(UNKNOWN.format(name=name) if name else "")
            return

        self.heading.setText(_shown(person) + ("  (you)" if person["is_owner"] else ""))
        self.subheading.setText(self._summary(person))
        # Nothing to delete from the database, and `forget_person` would refuse it
        # in those words anyway. Disabled rather than hidden, as everywhere else
        # here: a button that moves is a button you have to look for. Rename
        # follows the same rule: a drifted name has no record to rename.
        self.forget_button.setEnabled(person["in_database"])
        self.rename_button.setEnabled(person["in_database"])

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
            item.setIcon(self._meeting_icon)
            self.appearances.addItem(item)

        known = self._document["projects"]
        archived = set(self._document["archived"])
        for pid in person["tags"]:
            label_text = known.get(pid, f"{pid}?")
            # Marked here and nowhere else on a project's behalf: this is the
            # list that explains which section the person is sitting in.
            if pid in archived:
                label_text = f"{label_text} (archived)"
            item = QListWidgetItem(label_text)
            item.setData(TARGET_ROLE, pid)
            item.setIcon(self._project_icon)
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
        # The record first: the id is what `--forget` and `person rename` take,
        # and the email is the one field no other surface shows.
        record = f"id {person['id']}"
        if person.get("email"):
            record += f", {person['email']}"
        if person["inactive"]:
            return f"{record}. {INACTIVE_NOTE}"
        return (
            f"{record}. {person['prints']} voiceprint(s) filed from {filed} meeting(s); the "
            f"name appears in {appears}. Recognition files nothing new, so the second is "
            f"usually the larger."
        )

    # --- Going somewhere ----------------------------------------------------

    def select(self, name: str) -> None:
        """Show this person, or say that nobody is filed under the name.

        Called by the window when a label or a `[[Wikilink]]` is clicked. It
        refreshes first, because the click may be the first thing that has
        happened on this tab and the document behind it may never have been read
        — and resolves *after* the refresh, since what the text means depends on
        the directory just read. Two people it fits equally: the first is shown
        and both are named, which is information about the note rather than a
        decision made on its behalf.
        """
        # Cleared, or a name hidden by a leftover filter would look like a name
        # nobody is filed under — which is the one message on this page that must
        # never be shown wrongly.
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self._selected = None
        self.refresh()
        hits = self._matching(name)
        # An unmatched name is kept as the selection so the detail pane says
        # so in `UNKNOWN`'s words; `_person` finds nothing under it.
        self._selected = hits[0]["id"] if hits else name.strip()
        self._fill_list()
        if len(hits) > 1:
            self._complain(
                AMBIGUOUS.format(name=name.strip(), ids=", ".join(p["id"] for p in hits))
            )

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
        the same exception the projects page's Delete modal and the meetings
        page's two are, and for the same reason. What it says is the part
        somebody would otherwise assume the other way: the labels revert too,
        everywhere, and the snippets that would let you recognise the voice again
        are already gone.
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
        forgotten, message = label.forget_person(self.config, person["id"])
        if not forgotten:
            self._complain(message)
            return
        log.info("%s", message)
        self._selected = None
        self.refresh()

    def _on_rename(self) -> None:
        """Change what a person is called, through the one guarded function.

        Three fields prefilled with the record, and every rule — what a name
        may be, what an email may be, what propagates where, a short name that
        would collide inside one meeting — is :func:`referat.label.rename_person`'s
        and its refusal is shown unedited. The selection survives, because the
        id does.
        """
        person = self._person(self._selected)
        if person is None:
            return
        dialog = RenameDialog(self, person)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name, short, email = dialog.values()
        renamed, message = label.rename_person(
            self.config, person["id"], name=name, short=short, email=email
        )
        if not renamed:
            self._complain(message)
            return
        log.info("%s", message)
        self.refresh()
        self._complain(message)

    # --- Messages -----------------------------------------------------------

    def _complain(self, message: str) -> None:
        """Show a refusal in the words of whoever owns the rule, unedited."""
        self.message.setText(message)
        self.message.show()

    def _clear_message(self) -> None:
        self.message.clear()
        self.message.hide()


def _shown(person: dict[str, Any]) -> str:
    """The full name, and the short name after it when the two differ."""
    if person["short"] and person["short"] != person["name"]:
        return f"{person['name']} ({person['short']})"
    return person["name"]


class RenameDialog(QDialog):
    """Three fields over one record. Every rule lives in `label.rename_person`."""

    def __init__(self, parent: QWidget, person: dict[str, Any]) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Rename {person['name']}")
        self.name = QLineEdit(person["name"])
        self.short = QLineEdit(person["short"] if person["short"] != person["name"] else "")
        self.short.setPlaceholderText("blank: the full name")
        self.email = QLineEdit(person.get("email", ""))
        self.email.setPlaceholderText("optional")

        form = QFormLayout()
        form.addRow("Id", QLabel(person["id"]))
        form.addRow("Full name", self.name)
        form.addRow("Short name", self.short)
        form.addRow("Email", self.email)

        note = QLabel(RENAME_NOTE)
        note.setWordWrap(True)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(note)
        layout.addWidget(buttons)
        self.setLayout(layout)
        self.resize(460, self.sizeHint().height())

    def values(self) -> tuple[str, str, str]:
        """What was typed: full name, short name (blank for the full name), email."""
        return self.name.text().strip(), self.short.text().strip(), self.email.text().strip()


def _heading(text: str) -> QListWidgetItem:
    """A row that is shown and cannot be selected: the drifted-names section."""
    item = QListWidgetItem(text)
    item.setFlags(Qt.ItemFlag.NoItemFlags)
    return item
