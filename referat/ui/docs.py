"""Attaching a Google Doc to a project, by pasting its link.

Build step 20's **phase 7**, and the last phase of the command center. Everything
step 13 built was reachable only from a prompt, which made the digests the one
feature of Referat that the primary UI could not touch — and the projects page
was already the right place for them, since it is where a project's glossary and
its documents both live.

**The link is the input, not the id.** A Google Doc id is forty-four characters
out of the middle of a URL, and asking somebody to find it was asking a person to
do a parser's job. `gdocs.parse_doc_ref` takes the address bar or the Share
button's link; if it carries `?tab=`, that is the tab you were looking at and it
is preselected.

**The tabs are fetched and shown rather than typed.** The alternative is what the
CLI does — refuse, list the tabs in the refusal, and have somebody run the
command again with the right name — which is a reasonable thing at a prompt and a
bad thing in a window that could simply show them. So this makes one
`documents.get` and fills a combo from it.

**That fetch is on a thread**, because it is a network round trip and this
process owns the recorder: a hung request on the GUI thread is a window somebody
cannot stop a meeting from. The result comes back through a queued signal, which
is the rule this project paid three heap corruptions for.

**This dialog cannot authenticate and does not try.** Consent needs a terminal by
construction — see :mod:`referat.gdocs` — so a machine that has never consented
gets the sentence saying to run one command at a prompt, once. That is the
failure mode phase 7 was designed around rather than one it ran into.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from referat import gdocs
from referat.config import Config

log = logging.getLogger(__name__)

PASTE_HELP = (
    "Open the document, click the tab you want, and copy the address. A link with "
    "?tab= in it says which tab; otherwise pick one below."
)

SAME_TAB_HELP = (
    "You can keep writing in the tab you choose. Each meeting is bracketed by a "
    "small gray [referat:...] marker pair, and a sync only ever replaces what is "
    "between a matching pair -- anything you write around them stays."
)


class LinkDocDialog(QDialog):
    """Create a new digest doc, or attach one whose link you paste.

    Answers three things and writes nothing: whether to create, which document,
    and which tab. The write is :func:`referat.cli.link_doc`'s, called by the
    page — the same split every dialog here makes, and the reason the rules about
    what a link and a tab may be are not re-implemented in a widget.
    """

    looked_up = Signal(object, str)
    """`(tabs, complaint)` from the worker thread. `tabs` is `gdocs.tab_titles`'s."""

    def __init__(self, config: Config, project_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Link a Google Doc to {project_name}")
        self.setMinimumWidth(560)
        self._config = config
        self._gdoc_id = ""
        self._tabs: list[tuple[str, str, int]] = []
        self._looking_up = False

        self.create = QRadioButton("Create a new document")
        self.create.setChecked(True)
        self.create.toggled.connect(self._on_mode)
        self.attach = QRadioButton("Attach a document I already have")

        self.created_title = QLineEdit()
        self.created_title.setPlaceholderText(f"{project_name} Meeting Digest")

        self.link = QLineEdit()
        self.link.setPlaceholderText("https://docs.google.com/document/d/.../edit?tab=t.0")
        self.link.setClearButtonEnabled(True)
        self.link.textChanged.connect(self._on_link_changed)

        self.lookup_button = QPushButton("Look up its tabs")
        self.lookup_button.setAutoDefault(False)
        self.lookup_button.setEnabled(False)
        self.lookup_button.clicked.connect(self._lookup)

        self.tabs = QComboBox()
        self.tabs.setEnabled(False)
        self.tabs.currentIndexChanged.connect(lambda _index: self._refresh_ok())

        self.new_tab = QCheckBox(
            f"Add a new tab called {config.digest.new_tab_name!r} instead"
        )
        self.new_tab.setEnabled(False)
        self.new_tab.toggled.connect(self._on_new_tab)

        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)

        same_tab = QLabel(SAME_TAB_HELP)
        same_tab.setWordWrap(True)

        paste_help = QLabel(PASTE_HELP)
        paste_help.setWordWrap(True)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Link")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addWidget(self.create)
        layout.addWidget(self.created_title)
        layout.addSpacing(8)
        layout.addWidget(self.attach)
        layout.addWidget(paste_help)
        layout.addWidget(self.link)
        layout.addWidget(self.lookup_button)
        layout.addWidget(QLabel("Tab"))
        layout.addWidget(self.tabs)
        layout.addWidget(self.new_tab)
        layout.addWidget(same_tab)
        layout.addWidget(self.status)
        layout.addStretch(1)
        layout.addWidget(self.buttons)
        self.setLayout(layout)

        self.looked_up.connect(self._on_looked_up)
        self._on_mode()

    # --- The mode ----------------------------------------------------------

    def _on_mode(self) -> None:
        creating = self.create.isChecked()
        self.created_title.setEnabled(creating)
        for widget in (self.link, self.lookup_button):
            widget.setEnabled(not creating)
        if creating:
            self.tabs.setEnabled(False)
            self.new_tab.setEnabled(False)
            self.new_tab.setChecked(False)
        if creating:
            self.status.clear()
        else:
            self._on_link_changed(self.link.text())
        self._refresh_ok()

    def _on_new_tab(self, checked: bool) -> None:
        """Ticking it means the tab combo no longer decides anything.

        Adding a tab is a **visible change to somebody else's document**, so it
        is never what happens when a lookup finds nothing suitable -- the same
        rule that stops a tab being *picked* automatically. It is a box you tick.
        """
        self.tabs.setEnabled(bool(self._tabs) and not checked)
        self._refresh_ok()

    def _on_link_changed(self, text: str) -> None:
        """A link that parses enables the lookup, and one carrying a tab is remembered."""
        self._tabs = []
        self.tabs.clear()
        self.tabs.setEnabled(False)
        gdoc_id, tab = gdocs.parse_doc_ref(text)
        self._gdoc_id = gdoc_id
        self.lookup_button.setEnabled(bool(gdoc_id) and not self._looking_up)
        if text.strip() and not gdoc_id:
            self.status.setText("That does not look like a Google Doc link or id.")
        else:
            self.status.setText("" if not gdoc_id else f"Document {gdoc_id}")
        self._wanted_tab = tab
        self._refresh_ok()

    # --- The lookup --------------------------------------------------------

    def _lookup(self) -> None:
        """Fetch the document's tabs. On a thread, and the dialog says it is busy."""
        if not self._gdoc_id or self._looking_up:
            return
        self._looking_up = True
        self.lookup_button.setEnabled(False)
        self.status.setText("Looking up the document...")
        self._refresh_ok()
        threading.Thread(target=self._lookup_worker, args=(self._gdoc_id,), daemon=True).start()

    def _lookup_worker(self, gdoc_id: str) -> None:
        """Touches no widget. The result crosses back as a queued signal."""
        try:
            document = gdocs.get_document(self._config, gdoc_id)
        except gdocs.GoogleError as exc:
            self.looked_up.emit([], str(exc))
            return
        except Exception:  # noqa: BLE001 - a dialog may not take the process down
            log.exception("could not read the document %s", gdoc_id)
            self.looked_up.emit([], "Could not read that document - see the log.")
            return
        self.looked_up.emit(gdocs.tab_titles(document), "")

    def _on_looked_up(self, tabs: list[tuple[str, str, int]], complaint: str) -> None:
        self._looking_up = False
        self.lookup_button.setEnabled(bool(self._gdoc_id))
        if complaint:
            self.status.setText(complaint)
            self._refresh_ok()
            return
        self._tabs = tabs
        self.tabs.clear()
        for title, tab_id, depth in tabs:
            self.tabs.addItem(f"{'    ' * depth}{title or '(untitled)'}", tab_id)
        self.tabs.setEnabled(bool(tabs) and not self.new_tab.isChecked())
        self.new_tab.setEnabled(True)

        # **The tab is chosen and never guessed**, which is the CLI's rule and is
        # not relaxed just because a combo makes a default cheap. Two things may
        # decide it: the `?tab=` in the pasted link, which is somebody saying
        # which tab they were looking at, and a tab actually named `Meetings`.
        # Failing both, nothing is selected and the Link button stays disabled.
        #
        # Defaulting to the first tab was written first and was wrong on the very
        # first real document: its tabs are `Proposal` and `Meeting Notes &
        # Writing Log`, so the free default was the one tab a digest must not go
        # in. A wrong default that is *visible* is still a wrong default, because
        # the whole point of a dialog is that somebody clicks through it.
        chosen = -1
        for index, (title, tab_id, _) in enumerate(tabs):
            if self._wanted_tab and self._wanted_tab in (tab_id, title):
                chosen = index
                break
            if title.strip() == gdocs.MEETINGS_TAB and chosen < 0:
                chosen = index
        self.tabs.setCurrentIndex(chosen)
        count = f"{len(tabs)} tab{'s' if len(tabs) != 1 else ''}"
        if chosen < 0:
            self.status.setText(
                f"{count}. Pick the one to write into -- Referat writes there and "
                f"nowhere else in the document, and will not choose for you."
            )
        else:
            self.status.setText(
                f"{count}. Referat will write into the one selected and nowhere else."
            )
        self._refresh_ok()

    def _refresh_ok(self) -> None:
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        if self.create.isChecked():
            ok.setEnabled(True)
            return
        if self.new_tab.isChecked():
            # A new tab needs no choice from the combo -- there is nothing yet
            # to choose from.
            ok.setEnabled(bool(self._gdoc_id) and not self._looking_up)
            return
        ok.setEnabled(
            bool(self._gdoc_id)
            and self.tabs.isEnabled()
            and self.tabs.currentIndex() >= 0
            and not self._looking_up
        )

    # --- What the page reads back ------------------------------------------

    def result_link(self) -> dict[str, Any]:
        """The three answers, as :func:`referat.cli.link_doc`'s keywords."""
        if self.create.isChecked():
            return {
                "gdoc_id": "",
                "tab": "",
                "new_tab": False,
                "title": self.created_title.text().strip(),
            }
        if self.new_tab.isChecked():
            return {"gdoc_id": self._gdoc_id, "tab": "", "new_tab": True, "title": ""}
        return {
            "gdoc_id": self._gdoc_id,
            "tab": self.tabs.currentData() or "",
            "new_tab": False,
            "title": "",
        }
