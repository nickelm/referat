"""Striping a list, and the Qt bug that comes with it.

One function, and it exists so the two halves cannot be separated. Turning on
`alternatingRowColors` in Qt 6's `windows11` style **also changes how a selected
row is painted**, and the second half of that change is a bug: the row gets the
palette's `Highlight` — a strong blue — while its text stays in `Text` rather
than `HighlightedText`. Black on dark blue.

**The correction must not be applied to a view that does not stripe**, and that
is the whole reason this is a function rather than a line in `shell.py`. Without
striping the same style paints a *soft grey* selection and keeps the text dark,
which is perfectly readable — so an application-wide rule forcing
`highlighted-text` turns every unstriped list white-on-light-grey and invisible.
It was written that way first and broke the projects list and the people lists
exactly so.

Measured, by rendering a `QListWidget` to a pixmap and counting the very light
and very dark pixels in the selected row:

    striping   fix    background   dark px   light px
    on         off    #0067c0          491          0     unreadable
    on         on     #0067c0            0        291     correct
    off        off    #f5f5f5           72       3275     correct
    off        on     #f5f5f5            0       3865     invisible

So: striping and the correction are one act, and :func:`stripe` is that act. A
view that wants stripes calls this instead of `setAlternatingRowColors(True)`,
and there is no way to end up with one and not the other.

`palette(highlighted-text)` rather than a colour, so a dark theme still gets a
readable one — the same rule :mod:`referat.ui.icons` follows for glyphs.
"""

from __future__ import annotations

from PySide6.QtWidgets import QAbstractItemView

SELECTED_TEXT_FIX = "QAbstractItemView::item:selected { color: palette(highlighted-text); }"
"""The correction, scoped to the one view it is set on. See the module docstring."""


def stripe(view: QAbstractItemView) -> None:
    """Alternate this view's row colours, and keep its selected text readable.

    Everything that shows data in this window stripes; everything that does not
    is a list of headings. That is why the bug went unseen for four builds and
    then, once corrected in the wrong place, broke precisely the lists that had
    been fine.
    """
    view.setAlternatingRowColors(True)
    view.setStyleSheet(SELECTED_TEXT_FIX)
