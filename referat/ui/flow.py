"""A layout that wraps, because Qt does not ship one.

`QHBoxLayout` puts every widget on one line and makes the line as wide as it
needs to be. That is fine for four buttons and wrong for a gallery of names: the
speaker dialog's chips pushed the detail pane wider than the window as the voices
database grew, squeezing everything else, which is exactly the complaint this
answers.

This is the classic Qt *flow layout* — lay out left to right, wrap when the row
is full, and report a height that depends on the width. The only subtle part is
`heightForWidth`: a layout whose height depends on its width has to say so
through `hasHeightForWidth`, or the parent gives it one row's worth of space and
clips everything below.

Kept general enough to be a layout and no further: no spacing policy, no
alignment options, no per-item stretch. Simple and direct over general and
configurable.
"""

from __future__ import annotations

from PySide6.QtCore import QMargins, QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QLayoutItem, QSizePolicy, QWidget


class FlowLayout(QLayout):
    """Left to right, wrapping onto a new row when the current one is full."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 6) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self.setSpacing(spacing)
        self.setContentsMargins(QMargins(0, 0, 0, 0))

    # --- The QLayout contract -------------------------------------------------

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802  (Qt's own spelling)
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802
        """Neither. This grows downwards by wrapping, not by being given space."""
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._lay_out(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._lay_out(rect, apply=True)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        """The widest single item, not the sum: anything narrower simply wraps more."""
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(
            margins.left() + margins.right(), margins.top() + margins.bottom()
        )

    # --- The actual placing ---------------------------------------------------

    def _lay_out(self, rect: QRect, apply: bool) -> int:
        """Place the items in `rect` and return the height used.

        Called twice for two different jobs — once by `heightForWidth` to *ask*
        and once by `setGeometry` to *do* — which is why it is one function with
        a flag rather than two that would drift apart the first time the wrapping
        rule changed.
        """
        margins = self.contentsMargins()
        left = rect.x() + margins.left()
        top = rect.y() + margins.top()
        right = rect.right() - margins.right()

        x, y, row_height = left, top, 0
        for item in self._items:
            hint = item.sizeHint()
            if x > left and x + hint.width() > right:
                x = left
                y += row_height + self.spacing()
                row_height = 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self.spacing()
            row_height = max(row_height, hint.height())
        return y + row_height - rect.y() + margins.bottom()


def wrapping(parent: QWidget | None = None, spacing: int = 6) -> tuple[QWidget, FlowLayout]:
    """A widget whose children wrap, and the layout that does it.

    A helper because a `FlowLayout` almost always wants a host widget with a
    vertical size policy of `Minimum` — without it the parent layout hands the
    host a fixed height and the wrapped rows below the first are clipped, which
    looks exactly like the wrapping not working.
    """
    host = QWidget(parent)
    layout = FlowLayout(host, spacing)
    host.setLayout(layout)
    host.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
    return host, layout
