"""A project's recap in a window of its own, big enough to read.

Build step 24 put the recap in a pane under the projects page's documents,
where it shares one column with the description, the glossary and the doc list
— which is the right place to see *that* a recap exists and whether it is
stale, and the wrong place to read one. A recap is read minutes before the
meeting it is for, and often **during** it, which is exactly when a
three-hundred-pixel pane under a form is least use.

**A window and not a modal.** A modal would lock the command center, and the
command center holds the Stop button: a brief kept open through a meeting would
be a recording nobody could stop without closing it first. So this is a
separate top-level window with no owner — it can sit on a second monitor, go
behind the command center when a citation is followed, and be left open.

**It renders what the page renders and decides nothing.** The page hands it the
same `cli.recap_document` and the same :func:`referat.ui.projects.recap_markdown`
it draws its own pane from, and pushes a redraw here whenever it redraws there,
so a rebuild that finishes while this is open shows up in both. Links go back
through the page's own dispatch, so a citation means the same thing in both
places.

**Zoom is kept on this object, not stored anywhere**, and starts a few steps
up, because being bigger than the pane is the whole reason to open it.
`Ctrl+±` and `Ctrl+0` work here as they do in the viewer; they are this
window's own shortcuts, and do not clash with the command center's, because Qt
scopes a window shortcut to its window.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from referat.ui import icons, richtext

DEFAULT_ZOOM = 3
"""Steps above the pane's size the window opens at. Reading size, not form size."""


class RecapWindow(QWidget):
    """One project's recap, resizable and zoomable, beside the command center."""

    def __init__(self, on_anchor: Callable[[QUrl], None]) -> None:
        # No parent on purpose: an owned window stays above its owner, and this
        # one has to be able to go behind the command center when a citation in
        # it opens a meeting there.
        super().__init__(None, Qt.WindowType.Window)
        self._on_anchor = on_anchor
        self.project_id: str | None = None
        self._source = ""
        """The recap's Markdown as written, for a copy that is not a rendering."""
        self._zoom = 0

        self.heading = QLabel()
        self.heading.setTextFormat(Qt.TextFormat.PlainText)
        font = self.heading.font()
        font.setPointSizeF(font.pointSizeF() * 1.4)
        font.setBold(True)
        self.heading.setFont(font)
        self.state = QLabel()
        self.state.setWordWrap(True)

        self.text = QTextBrowser()
        self.text.setOpenLinks(False)
        self.text.setOpenExternalLinks(False)
        self.text.anchorClicked.connect(self._on_link)

        color = self.palette().windowText().color().name()
        copy_markdown = QAction("Copy as Markdown\tCtrl+Shift+C", self)
        copy_markdown.triggered.connect(lambda _checked=False: self.copy(False))
        copy_formatted = QAction("Copy as formatted text\tCtrl+Alt+C", self)
        copy_formatted.triggered.connect(lambda _checked=False: self.copy(True))
        copy_button = QToolButton()
        copy_button.setIcon(icons.glyph("copy", color))
        copy_button.setToolTip("Copy this recap (Ctrl+Shift+C)")
        copy_button.setAutoRaise(True)
        copy_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        copy_button.clicked.connect(lambda _checked=False: self.copy(False))
        menu = QMenu(copy_button)
        menu.addAction(copy_markdown)
        menu.addAction(copy_formatted)
        copy_button.setMenu(menu)

        zoom_out = QToolButton()
        zoom_out.setText("A-")
        zoom_out.setToolTip("Smaller (Ctrl+-)")
        zoom_out.setAutoRaise(True)
        zoom_out.clicked.connect(lambda _checked=False: self.zoom(-1))
        zoom_in = QToolButton()
        zoom_in.setText("A+")
        zoom_in.setToolTip("Larger (Ctrl++)")
        zoom_in.setAutoRaise(True)
        zoom_in.clicked.connect(lambda _checked=False: self.zoom(1))

        title_row = QHBoxLayout()
        title_row.addWidget(self.heading, 1)
        title_row.addWidget(zoom_out)
        title_row.addWidget(zoom_in)
        title_row.addWidget(copy_button)

        layout = QVBoxLayout()
        layout.addLayout(title_row)
        layout.addWidget(self.state)
        layout.addWidget(self.text, 1)
        self.setLayout(layout)

        # Ctrl+= as well as Ctrl++, for the reason the command center gives: +
        # is a shifted key on most layouts.
        for keys, steps in (("Ctrl++", 1), ("Ctrl+=", 1), ("Ctrl+-", -1), ("Ctrl+0", 0)):
            action = QAction(self)
            action.setShortcut(keys)
            action.triggered.connect(lambda _checked=False, n=steps: self.zoom(n))
            self.addAction(action)
        for keys, formatted in (("Ctrl+Shift+C", False), ("Ctrl+Alt+C", True)):
            action = QAction(self)
            action.setShortcut(keys)
            action.triggered.connect(lambda _checked=False, f=formatted: self.copy(f))
            self.addAction(action)
        close = QAction(self)
        close.setShortcut(QKeySequence(Qt.Key.Key_Escape))
        close.triggered.connect(self.close)
        self.addAction(close)

        self.resize(900, 820)
        self.zoom(DEFAULT_ZOOM)

    def show_recap(
        self,
        project_id: str,
        name: str,
        state: str,
        markdown: str,
        source: str,
        *,
        reasons: str = "",
    ) -> None:
        """Draw one project's recap. The same project again keeps the scroll.

        Redrawn in place when the page redraws, so a rebuild that lands while
        this is open does not throw the reader back to the top — the same rule
        :meth:`referat.ui.viewer.Viewer.show_meeting` keeps for a meeting.
        """
        same = project_id == self.project_id
        scroll = self.text.verticalScrollBar().value() if same else 0
        self.project_id = project_id
        self._source = source
        self.setWindowTitle(f"Recap - {name}")
        self.heading.setText(name)
        self.state.setText(state)
        self.state.setToolTip(reasons)
        self.text.setMarkdown(markdown)
        self.text.verticalScrollBar().setValue(scroll)

    def zoom(self, steps: int) -> None:
        """Grow or shrink the text, or reset it to the pane's size when 0."""
        if steps == 0:
            steps = -self._zoom
        self._zoom += steps
        if steps > 0:
            self.text.zoomIn(steps)
        elif steps < 0:
            self.text.zoomOut(-steps)

    def copy(self, formatted: bool) -> None:
        """The recap's source onto the clipboard, as the viewer copies a note."""
        if self._source:
            richtext.to_clipboard(self._source, formatted=formatted)

    def _on_link(self, url: QUrl) -> None:
        """Follow a link through the page's own dispatch. The page raises its window."""
        self._on_anchor(url)

    def present(self) -> None:
        """Show, un-minimize and raise, whether or not it was already open."""
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()
