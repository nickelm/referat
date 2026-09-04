"""The action items page: what the meetings decided somebody would do.

The window's three entity tabs are meetings, projects and people; this is not a
fourth. An action item is a *derivative* of one meeting's notes, which is why it
sits immediately right of Meetings — its provenance is one tab to the left, and
one tab to the left is exactly where activating a row sends you.

**It shows one person's list at a time**, and that is the whole shape of the
page. `/cleanup` assigns an owner to every item it writes, so the parse gets
everybody's for free; throwing away the other sixty-eight to show eleven would be
discarding something already paid for. The picker defaults to
`[speakers].owner_name`, offers every owner it actually found with a count, and
keeps *Unassigned* and *Everyone* as their own entries. The **dashboard box is
always and only the owner's** — that split is deliberate, and it is what stops
this page from having to be two pages.

**It is fed, not self-refreshing**, and that is a deliberate deviation from
:class:`referat.ui.projects.ProjectsPage` and :class:`referat.ui.people.PeoplePage`,
which read for themselves when they are switched to. Those two cost a scan of
both meeting roots each; this one is handed the document the window has already
built, the way the dashboard is, so an extra tab costs the notes files and
nothing more. It is therefore **not** in :meth:`referat.ui.window.CommandCenter._refresh_page`,
and putting it there would silently buy a fifth scan.

**Nothing here implements a rule.** Every write goes out through
:mod:`referat.cli` — `set_action_done`, `dismiss_action`, `edit_action`,
`prune_actions` — which is where the key resolution, the unreadable-file guard
and the single save live. A refusal comes back as a :class:`referat.cli.Outcome`
and is shown in the words its owner wrote.

**Nothing here writes `notes.md`.** *Drop* hides an item; it does not delete the
line. The note is the record of what the meeting produced, and an item somebody
drops because it was never really one is still a thing the meeting produced.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from referat import actions as actions_model
from referat import cli
from referat.ui import icons, lists, richtext

KEY_ROLE = Qt.ItemDataRole.UserRole
"""The item key a row stands for, so nothing parses a rendered cell back."""

AT_ROLE = Qt.ItemDataRole.UserRole + 1
MEETING_ROLE = Qt.ItemDataRole.UserRole + 2
OWNERS_ROLE = Qt.ItemDataRole.UserRole + 3

COLUMNS = ("", "Owner", "Due", "Meeting", "Item")

EMPTY = (
    "No action items yet. They are the `## Action items` section of a meeting's "
    "notes.md, written by Generate notes... on the Meetings tab."
)


class ActionsPage(QWidget):
    """One person's action items, across every meeting."""

    meeting_requested = Signal(str, float)
    """A row was activated: open this meeting, scrolled to this second.

    A negative second means the item cited no timestamp, which the window reads
    as *do not scroll* — landing on the notes rather than jumping the viewer to
    the transcript tab for no reason.
    """

    person_requested = Signal(str)
    """An owner was followed to their page, from the context menu."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._document: dict[str, Any] = {
            "owner": "",
            "complaint": "",
            "items": [],
            "orphans": [],
            "counts": {},
        }
        self._filling = False
        """Set while the tree is being rebuilt, so `itemChanged` is not a click.

        A `QTreeWidget` emits `itemChanged` for every checkbox it is *given* as
        well as every one a person ticks. Without this the first redraw after a
        tick would write every visible row back to disk.
        """

        self.person = QComboBox()
        self.person.setMinimumWidth(200)
        self.person.currentIndexChanged.connect(self._on_filter)

        self.show_done = QCheckBox("Show done")
        self.show_done.toggled.connect(self._on_filter)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search items")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._on_filter)

        filters = QHBoxLayout()
        filters.setContentsMargins(0, 0, 0, 0)
        filters.addWidget(QLabel("Showing"))
        filters.addWidget(self.person)
        filters.addWidget(self.show_done)
        filters.addWidget(self.search, 1)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(len(COLUMNS))
        self.tree.setHeaderLabels(list(COLUMNS))
        self.tree.setRootIsDecorated(False)
        lists.stripe(self.tree)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_menu)
        self.tree.itemChanged.connect(self._on_checked)
        self.tree.itemActivated.connect(self._on_activated)
        self.tree.itemSelectionChanged.connect(self._on_selection)
        header = self.tree.header()
        header.setStretchLastSection(True)
        for column in range(len(COLUMNS) - 1):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)

        self.edit_button = QPushButton("Edit...")
        self.edit_button.setToolTip(
            "Correct this item's wording. notes.md is not changed - the correction is "
            "stored beside it, and clearing the box puts the note's own wording back."
        )
        self.edit_button.clicked.connect(self._on_edit)
        self.drop_button = QPushButton("Drop")
        self.drop_button.setToolTip(
            "Hide this item. It stays in the meeting's notes.md, which is the record "
            "of what the meeting produced; Show done and `referat actions restore` "
            "both bring it back."
        )
        self.drop_button.clicked.connect(self._on_drop)
        self.copy_button = QPushButton("Copy")
        self.copy_button.setIcon(self._glyph("page"))
        self.copy_button.setToolTip(
            "Put what is shown on the clipboard as a Markdown task list, for pasting "
            "into your own TODO."
        )
        self.copy_button.clicked.connect(lambda _checked=False: self.copy_shown())

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.addStretch(1)
        buttons.addWidget(self.edit_button)
        buttons.addWidget(self.drop_button)
        buttons.addWidget(self.copy_button)

        self.footer = QLabel()
        self.footer.setWordWrap(True)
        self.footer.setTextFormat(Qt.TextFormat.PlainText)

        self.orphans = QPushButton("Review...")
        self.orphans.setToolTip(
            "Ticks whose item no longer appears in any notes.md, because a later "
            "/cleanup re-worded it."
        )
        self.orphans.clicked.connect(self._on_orphans)
        self.orphans.hide()

        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.addWidget(self.footer, 1)
        bottom.addWidget(self.orphans)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(filters)
        layout.addWidget(self.tree, 1)
        layout.addLayout(bottom)
        layout.addLayout(buttons)
        self.setLayout(layout)
        self._on_selection()

    def _glyph(self, name: str) -> Any:
        return icons.glyph(name, self.palette().windowText().color().name())

    # --- Being fed ----------------------------------------------------------

    def set_document(self, document: dict[str, Any]) -> None:
        """Redraw from the document the window has already built.

        Not `refresh()`, and the difference is the same one the dashboard makes:
        every other page reads when it is switched to, and this one is handed
        what has already been read.
        """
        self._document = document
        self._fill_people()
        self._fill()

    def select_person(self, name: str) -> None:
        """Show one person's list, from a link somewhere else in the window.

        Adds the name if the picker does not have it — somebody with no open
        items still has a page worth landing on, and a link that silently showed
        the wrong person's list would be worse than one that showed an empty one.
        """
        index = self.person.findData(name)
        if index < 0:
            self.person.addItem(name, name)
            index = self.person.count() - 1
        self.person.setCurrentIndex(index)

    def clear_filters(self) -> None:
        """Drop anything that could hide a row somebody has just been sent to."""
        for widget, reset in (
            (self.search, self.search.clear),
            (self.show_done, lambda: self.show_done.setChecked(False)),
        ):
            widget.blockSignals(True)
            reset()
            widget.blockSignals(False)

    def _fill_people(self) -> None:
        """The picker: every owner with a count, then Unassigned, then Everyone.

        Rebuilt on every document and the selection restored by *name* rather
        than by index, because the counts reorder the list as work is ticked off
        and an index would quietly start pointing at somebody else.
        """
        owner = self._document["owner"]
        wanted = self.person.currentData() or owner
        counts = dict(cli.owner_counts(self._document))

        self.person.blockSignals(True)
        self.person.clear()
        names = [name for name, _ in cli.owner_counts(self._document)]
        if owner and owner not in names:
            # The owner is always offered, even with nothing open. A picker that
            # dropped you off it the moment you cleared your list would look
            # exactly like a bug.
            names.insert(0, owner)
        for name in names:
            if name == actions_model.UNASSIGNED:
                continue
            label = f"{name} ({counts.get(name, 0)})"
            self.person.addItem(f"{label} - you" if name == owner else label, name)
        if actions_model.UNASSIGNED in counts:
            self.person.addItem(
                f"{actions_model.UNASSIGNED} ({counts[actions_model.UNASSIGNED]})",
                actions_model.UNASSIGNED,
            )
        self.person.addItem(f"Everyone ({len(self._document['items'])})", cli.EVERYONE)
        index = self.person.findData(wanted)
        self.person.setCurrentIndex(max(0, index))
        self.person.blockSignals(False)

    def _shown(self) -> list[dict[str, Any]]:
        """The items on screen: the picker, the done toggle, then the search box."""
        items = cli.select_actions(
            self._document,
            person=self.person.currentData() or "",
            include_done=self.show_done.isChecked(),
        )
        needle = self.search.text().strip().casefold()
        if needle:
            items = [
                i
                for i in items
                if needle in f"{i['text']} {i['owner_text']} {i['meeting']}".casefold()
            ]
        return items

    def _fill(self) -> None:
        complaint = self._document["complaint"]
        for control in (self.edit_button, self.drop_button, self.orphans):
            control.setEnabled(not complaint)

        self._filling = True
        self.tree.clear()
        items = self._shown()
        for item in items:
            row = QTreeWidgetItem(
                [
                    "",
                    item["owner_text"],
                    item["due"] or "",
                    item["meeting"],
                    item["text"],
                ]
            )
            row.setData(0, KEY_ROLE, item["key"])
            row.setData(0, AT_ROLE, item["at"][0] if item["at"] else -1.0)
            row.setData(0, MEETING_ROLE, item["meeting"])
            row.setData(0, OWNERS_ROLE, list(item["owners"]))
            row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            row.setCheckState(
                0, Qt.CheckState.Checked if item["done"] else Qt.CheckState.Unchecked
            )
            tip = [item["text"]]
            if item["qualifier"]:
                tip.append(item["qualifier"])
            if item["edited"]:
                tip.append(f"notes.md says: {item['original']}")
            row.setToolTip(4, "\n\n".join(tip))
            if item["edited"]:
                row.setText(4, item["text"] + "  (edited)")
            self.tree.addTopLevelItem(row)
        self._filling = False

        for column in range(len(COLUMNS) - 1):
            self.tree.resizeColumnToContents(column)
        self._fill_footer(len(items))
        self._on_selection()

    def _fill_footer(self, shown: int) -> None:
        if self._document["complaint"]:
            self.footer.setText(self._document["complaint"])
            self.orphans.hide()
            return
        counts = self._document["counts"]
        if not self._document["items"]:
            self.footer.setText(EMPTY)
            self.orphans.hide()
            return
        whose = self.person.currentData() or self._document["owner"]
        who = "everybody" if whose == cli.EVERYONE else whose
        text = (
            f"{shown} shown for {who}; {counts.get('open', 0)} open in all, "
            f"{counts.get('mine', 0)} of them {self._document['owner']}'s"
        )
        orphans = counts.get("orphans", 0)
        if orphans:
            one = orphans == 1
            text += (
                f". {orphans} stored item{'' if one else 's'} no longer "
                f"{'appears' if one else 'appear'} in any notes.md"
            )
            self.orphans.show()
        else:
            self.orphans.hide()
        self.footer.setText(text)

    # --- Doing something to one ---------------------------------------------

    def _selected(self) -> list[QTreeWidgetItem]:
        return self.tree.selectedItems()

    def _on_selection(self) -> None:
        one = len(self._selected()) == 1
        enabled = not self._document["complaint"]
        self.edit_button.setEnabled(one and enabled)
        self.drop_button.setEnabled(bool(self._selected()) and enabled)

    def _on_filter(self) -> None:
        self._fill()

    def _on_checked(self, row: QTreeWidgetItem, column: int) -> None:
        """A checkbox was clicked. Ticking is the one write with no confirmation.

        It is reversible in one click, which is the whole test for whether
        something needs a modal in front of it.
        """
        if self._filling or column != 0:
            return
        done = row.checkState(0) == Qt.CheckState.Checked
        self._apply(cli.set_action_done(self._parent_window_config(), row.data(0, KEY_ROLE), done))

    def _on_edit(self) -> None:
        rows = self._selected()
        if len(rows) != 1:
            return
        key = rows[0].data(0, KEY_ROLE)
        item = next((i for i in self._document["items"] if i["key"] == key), None)
        if item is None:
            return
        text, ok = QInputDialog.getText(
            self,
            "Edit action item",
            "Wording (clear the box to go back to what the notes say):",
            QLineEdit.EchoMode.Normal,
            item["text"],
        )
        if not ok:
            return
        self._apply(cli.edit_action(self._parent_window_config(), key, text.strip()))

    def _on_drop(self) -> None:
        rows = self._selected()
        if not rows:
            return
        answer = QMessageBox.question(
            self,
            "Drop action item" + ("s" if len(rows) != 1 else ""),
            f"Hide {len(rows)} action item{'s' if len(rows) != 1 else ''} from these lists?\n\n"
            "This does not change any notes.md. The item stays in the meeting's notes, "
            "which are the record of what the meeting produced; only this list forgets it.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        config = self._parent_window_config()
        for row in rows:
            outcome = cli.dismiss_action(config, row.data(0, KEY_ROLE), True)
            if not outcome.ok:
                self._apply(outcome)
                return
        self._apply(cli.Outcome(True, ""))

    def copy_shown(self, *, formatted: bool = False) -> bool:
        """Put what is shown on the clipboard. True if anything was.

        Public because the window's two copy shortcuts reach it: they belong to
        whichever page is in front, and this is what this page does with them.

        `cli.actions_markdown` over the very list the tree is drawing, so what is
        copied is exactly what is on screen — a Copy button that exported a
        different set than the one being looked at would be wrong in the way that
        is hardest to notice.

        **Markdown either way**; `formatted` decides only which clipboard flavour
        it is written as, through :func:`referat.ui.richtext.to_clipboard` — the
        one place that decision is made, so this page and the document viewer
        cannot come to disagree about what a copy puts on a clipboard. The button
        stays the plain one, since a list of action items is on its way to a
        message far more often than to a Google Doc.
        """
        items = self._shown()
        if not items:
            return False
        richtext.to_clipboard(cli.actions_markdown(items), formatted=formatted)
        return True

    def _on_orphans(self) -> None:
        orphans = self._document["orphans"]
        if not orphans:
            return
        listing = "\n".join(f"  {o['meeting']}: {o['seen'] or o['key']}" for o in orphans[:12])
        more = "" if len(orphans) <= 12 else f"\n  ... and {len(orphans) - 12} more"
        answer = QMessageBox.question(
            self,
            "Forget orphaned items",
            f"{len(orphans)} stored item(s) no longer appear in any notes.md, because a "
            f"later /cleanup re-worded them. What they used to say:\n\n{listing}{more}\n\n"
            "Forget this stored state? The notes themselves are not touched.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._apply(cli.prune_actions(self._parent_window_config()))

    def _on_activated(self, row: QTreeWidgetItem, _column: int) -> None:
        self.meeting_requested.emit(row.data(0, MEETING_ROLE), float(row.data(0, AT_ROLE)))

    def _on_menu(self, point: Any) -> None:
        """Follow an owner to their page.

        A context menu rather than a click on the cell, so it does not fight row
        activation — which means the meeting, and is the thing somebody wants far
        more often.
        """
        row = self.tree.itemAt(point)
        if row is None:
            return
        owners = row.data(0, OWNERS_ROLE) or []
        if not owners:
            return
        menu = QMenu(self)
        for name in owners:
            menu.addAction(f"Open {name}").triggered.connect(
                lambda _checked=False, n=name: self.person_requested.emit(n)
            )
        menu.exec(self.tree.viewport().mapToGlobal(point))

    # --- Reporting ----------------------------------------------------------

    def _parent_window_config(self) -> Any:
        """The config, from the window that owns this page.

        Reached through the parent rather than held, so there is one config in
        this process and this page cannot come to hold a stale one.
        """
        window = self.window()
        return window.app.config

    def _apply(self, outcome: cli.Outcome) -> None:
        """Report a refusal in its owner's words, then ask the window to redraw.

        The message is unprefixed, exactly as it reaches the CLI, because the
        sentence belongs to whatever rule refused and this has nothing to add to
        it.
        """
        if not outcome.ok:
            QMessageBox.warning(self, "Referat", outcome.message)
        window = self.window()
        refresh = getattr(window, "refresh", None)
        if callable(refresh):
            refresh()
