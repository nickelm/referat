"""The tag picker: which projects one meeting carries, and creating one inline.

Phase 2 of build step 20, and the first thing the command center ever writes.
It writes tags and only tags, through :func:`referat.cli.apply_tags` and
:func:`referat.cli.create_project` — never `projects.add_tags` directly, because
the guard, the refusal, the resolve and the save around it are the rule, and
putting them in a dialog would be the second implementation the one-implementation
rule exists to prevent. This module opens no file.

**Two sets, not one, and that is the whole design.** :attr:`TagPicker._carried`
is what the meeting already has — the baseline, never mutated — and
:attr:`TagPicker._checked` is the tick state, which *grows* when a project is
created in here. The VS Code picker shipped with one set serving as both, so a
project created inside it was already in the baseline by the time the diff ran,
landed in neither `added` nor `removed`, and `referat tag` was never called for
it: the project existed and the meeting stayed untagged. `TODO.md` wrote that
bug into this phase's specification so it would be prevented rather than
rediscovered, which is what these two attributes are.

**`_checked` is the only truth; the list widget is a view of it.** Filtering
hides rows and never rebuilds, creating appends one row, and nothing ever reads
the check states back in bulk. That is deliberate: rebuilding a checkable list
and then restoring its check states races the widget, which is the Qt form of
the lesson `referat-vscode/src/projects.ts` records. The workaround that file
needs — reopening the picker after a create, because assigning `picker.items`
makes VS Code recompute the ticked rows — has **no counterpart here** and must
not be copied: a `QListWidgetItem` owns its check state and nothing recomputes
it.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from referat import cli
from referat.config import Config

log = logging.getLogger(__name__)

ID_ROLE = Qt.ItemDataRole.UserRole
"""The project id, carried on the row so nothing parses a display name back."""

ORPHAN_SUFFIX = " (orphan - no project answers to this id)"
"""What an id the meeting carries but no project resolves is labelled.

Listed at all — and ticked — because otherwise it would be the one tag this
dialog could not take off, which is precisely the tag somebody opened it for.
`referat untag` validates nothing for the same reason.
"""


class TagPicker(QDialog):
    """Which projects one meeting carries. Ticks, unticks, and creates."""

    def __init__(
        self,
        parent: QWidget | None,
        config: Config,
        meeting_id: str,
        carried: list[str],
        known: dict[str, str],
    ) -> None:
        super().__init__(parent)
        self.config = config
        self.meeting_id = meeting_id

        self._carried = list(carried)
        self._checked = set(carried)
        self._known = dict(known)
        self._building = False
        self.created = False
        """Whether a project was made in here, whether or not it was applied.

        The window refreshes on this as well as on acceptance: a project created
        and then abandoned still exists, and a list that did not show it would be
        stale in exactly the way the sidebar's was.
        """

        self.setWindowTitle(f"Projects - {meeting_id}")
        self.resize(480, 460)

        self.field = QLineEdit()
        self.field.setPlaceholderText("Filter, or type the name of a new project")
        self.field.textChanged.connect(self._on_text)
        self.field.returnPressed.connect(self._on_return)

        self.create_button = QPushButton("Create")
        self.create_button.setEnabled(False)
        self.create_button.setAutoDefault(False)
        self.create_button.clicked.connect(self._on_create)

        self.list = QListWidget()
        self.list.itemChanged.connect(self._on_item_changed)

        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.hide()

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel
        )
        apply_button = self.buttons.button(QDialogButtonBox.StandardButton.Apply)
        apply_button.setDefault(True)
        # Both off, or Enter in the field would fire the field's own handler and
        # then the default button as well, so typing a new name and pressing
        # Enter would create the project *and* close the dialog in one keystroke.
        for button in self.buttons.buttons():
            button.setAutoDefault(False)
        apply_button.clicked.connect(self._on_apply)
        self.buttons.rejected.connect(self.reject)

        top = QHBoxLayout()
        top.addWidget(self.field, 1)
        top.addWidget(self.create_button)

        layout = QVBoxLayout()
        layout.addWidget(QLabel("Projects this meeting carries:"))
        layout.addWidget(self.list, 1)
        layout.addLayout(top)
        layout.addWidget(self.message)
        layout.addWidget(self.buttons)
        self.setLayout(layout)

        self._fill()

    # --- Building the list --------------------------------------------------

    def _rows(self) -> list[tuple[str, str]]:
        """Every id to offer, as `(id, label)`, ordered by what a person reads.

        Known projects by display name, then the orphans the meeting carries.
        Orphans last and marked, because an id with no project behind it is a
        different kind of thing from a project and should not sort in among them.
        """
        known = sorted(self._known.items(), key=lambda kv: kv[1].casefold())
        orphans = [pid for pid in self._carried if pid not in self._known]
        return [*known, *((pid, pid + ORPHAN_SUFFIX) for pid in orphans)]

    def _fill(self) -> None:
        """Build the list once, from :attr:`_checked`. The only full build there is."""
        self._building = True
        try:
            self.list.clear()
            for pid, label in self._rows():
                self.list.addItem(self._item(pid, label))
        finally:
            self._building = False

    def _item(self, pid: str, label: str) -> QListWidgetItem:
        """One row, checked from :attr:`_checked` and never from the widget."""
        item = QListWidgetItem(label)
        item.setData(ID_ROLE, pid)
        # Both are needed: a QListWidgetItem draws no checkbox at all unless it
        # is user-checkable *and* has been given an initial check state.
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(
            Qt.CheckState.Checked if pid in self._checked else Qt.CheckState.Unchecked
        )
        return item

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        """Mirror a tick into :attr:`_checked`.

        Guarded, because `itemChanged` fires for any data role — including the
        `setText` and `setData` of building a row — and an unguarded handler
        would rewrite the set from the widget it is supposed to be driving.
        """
        if self._building:
            return
        pid = str(item.data(ID_ROLE))
        if item.checkState() is Qt.CheckState.Checked:
            self._checked.add(pid)
        else:
            self._checked.discard(pid)

    # --- Filtering ----------------------------------------------------------

    def _on_text(self, text: str) -> None:
        """Hide the rows that do not match, and offer to create what was typed.

        Hiding rather than rebuilding: no rebuild means no restoring check
        states, which means none of the races this module's docstring is about —
        and a project that is ticked but filtered out of sight cannot be lost.
        """
        wanted = text.strip()
        self.create_button.setText(f'Create "{wanted}"' if wanted else "Create")
        self.create_button.setEnabled(bool(wanted))
        needle = wanted.casefold()
        for row in range(self.list.count()):
            item = self.list.item(row)
            item.setHidden(bool(needle) and needle not in item.text().casefold())

    def _on_return(self) -> None:
        """Enter creates when there is a name to create, and applies otherwise."""
        if self.create_button.isEnabled():
            self._on_create()
        else:
            self._on_apply()

    # --- Creating -----------------------------------------------------------

    def _on_create(self) -> None:
        """Make the typed project, tick it, and leave the dialog open.

        The id comes back **from the command that made it** and is never looked
        up by display name: it is `slugify` plus a `-2` collision suffix, two
        projects are allowed to share a name, and matching a name back would
        silently pick the older one. In process there is no JSON in the way —
        :func:`referat.cli.create_project` hands over the `Project` itself.

        The label stored is the project's **own** name rather than the typed
        string, because Python collapses whitespace on the way in and a dialog
        that showed something `referat project list` disagreed with would be the
        UI holding an opinion about somebody else's field.

        The new row enters :attr:`_checked` and never :attr:`_carried`. That is
        the bug this whole module is shaped around.
        """
        name = self.field.text().strip()
        if not name:
            return
        project, complaint = cli.create_project(self.config, name)
        if project is None:
            self._complain(complaint)
            return
        self.created = True
        self._known[project.id] = project.name
        self._checked.add(project.id)
        self._clear_message()
        self.field.clear()
        # Rebuild rather than insert, so the new project lands in name order
        # rather than at the end. Safe only because every row is rebuilt from
        # `_checked`, which the new id is already in.
        self._fill()
        self._on_text("")
        self._select(project.id)

    def _select(self, pid: str) -> None:
        for row in range(self.list.count()):
            if str(self.list.item(row).data(ID_ROLE)) == pid:
                self.list.setCurrentRow(row)
                return

    # --- Applying -----------------------------------------------------------

    def _on_apply(self) -> None:
        """Diff the two sets, write once, and stay open on a refusal.

        Intercepted rather than wired to `accept`, so a refusal — another process
        deleting a project between opening this and pressing Apply, or a
        `projects.json` that stopped parsing in between — keeps the dialog up
        with the ticks intact instead of closing over the top of it.
        """
        added = [pid for pid, _ in self._rows() if pid in self._checked and pid not in self._carried]
        removed = [pid for pid in self._carried if pid not in self._checked]
        if not added and not removed:
            self.accept()
            return
        outcome = cli.apply_tags(self.config, self.meeting_id, add=added, remove=removed)
        if not outcome.ok:
            self._complain(outcome.message)
            return
        # Logged rather than shown: in a window the visible result is the row's
        # Projects cell and the recounted status bar, and a three-line report has
        # nowhere to live that the next refresh would not wipe.
        log.info("%s", outcome.message)
        self.accept()

    def _complain(self, message: str) -> None:
        """Show a refusal in the words of whoever owns the rule, unedited."""
        self.message.setText(message)
        self.message.show()

    def _clear_message(self) -> None:
        self.message.clear()
        self.message.hide()


def open_for(parent: QWidget, config: Config, meeting_id: str, carried: list[str]) -> bool:
    """Run the picker over one meeting. True when anything may have changed.

    Reads the project names **fresh**, every time, through
    :func:`referat.cli.project_names`. The sidebar's picker took them from the
    listing the view already held and offered one of two projects, because
    nothing invalidated that cache; here the read is one file and there is no
    cache to be wrong.

    A `projects.json` that will not parse refuses the dialog outright, in the
    sentence `referat tag` uses, because with no names loaded every tag would
    render as an orphan — a worse thing to put in front of somebody than a
    refusal. The cost is that a pure *removal*, which the CLI would still allow,
    cannot be done here either; recorded in `TODO.md` rather than papered over.
    """
    known, complaint = cli.project_names(config)
    if complaint:
        QMessageBox.warning(parent, "Projects", complaint)
        return False
    picker = TagPicker(parent, config, meeting_id, carried, known)
    accepted = picker.exec() == QDialog.DialogCode.Accepted
    return accepted or picker.created
