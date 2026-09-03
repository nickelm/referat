"""Every icon Referat draws, in QPainter and never as a bundled asset.

The notification-area icon came first, replacing the Pillow drawing that lived in
:mod:`referat.tray` until step 20, with two rules the rest of this module now
follows as well: the **colour carries the state**, and the **shape carries a run
that lost a channel** — a ring rather than a disc, because a ring reads as wrong
at sixteen pixels in a way that a slightly different red does not, and because
for a meeting held in a room a lost microphone means nothing is being recorded at
all.

**The window's icons are the same two rules one layer out.** A meeting's row
carries a dot in its lifecycle's colour, hollowed into a ring by exactly the
condition that hollows the tray's — so the taskbar and the meetings list say the
same thing in the same way, and neither had to be taught the other's vocabulary.
The glyphs on the buttons and the tabs are the third kind and carry no state at
all: they are drawn in the colour they are asked for, which callers take from
their own palette so a dark theme does not get black icons on a dark tab bar.

**Drawn rather than shipped**, and that is a dependency decision rather than an
aesthetic one. An icon font or an SVG set is a package in `pyproject.toml`, and
the base install is on the recording path — every file in it is something Smart
App Control can one day refuse. Fourteen glyphs of `QPainter` are three hundred lines
that cannot be blocked, cannot be missing at runtime, and scale to whatever DPI
the window is opened on. It is also the last place a raster asset would still be
readable: these are drawn at 64 px and asked for at 16.

Pure Qt, so it holds to the portability rule in :mod:`referat.ui`. Everything is
cached, because `setIcon` is called on every transition and on every row of every
refresh, and repainting a pixmap per call would be work for nothing.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap, QPolygonF

from referat.state import State

ICON_SIZE = 64
"""Drawn once at 64 px and scaled down by Qt. Windows asks for 16 and 32."""

COLORS: dict[State, str] = {
    State.IDLE: "#808080",
    State.RECORDING: "#d02020",
    State.PAUSED: "#e0b020",
    State.STOPPED: "#e88020",
    State.TRANSCRIBING: "#3080d8",
}

TOOLTIPS: dict[State, str] = {
    State.IDLE: "Referat - idle",
    State.RECORDING: "Referat - recording",
    State.PAUSED: "Referat - paused",
    State.STOPPED: "Referat - stopping",
    State.TRANSCRIBING: "Referat - transcribing",
}

LIFECYCLE: dict[str, str] = {
    "recording": "#d02020",
    "recorded": "#808080",
    "transcribing": "#3080d8",
    "gate_failed": "#e88020",
    "failed": "#a01515",
    "transcribed": "#7ab648",
    "notes_written": "#2f9e56",
    "synced": "#1f7a44",
}
"""One colour per value of `meta.json`'s `status`, and nothing derived.

The two *in flight* states borrow the recorder's own colours, so a red dot means
the same thing in the meetings list as it does in the notification area. The two
that want somebody are warm — `gate_failed` is a meeting stuck in staging and
`failed` is a transcription that raised — and the three that are finished are one
green deepening as the meeting moves through `transcribed`, `notes_written` and
`synced`, which is the only place in this UI where a colour carries an *order*.
`recorded` is grey because it is the honest absence of work rather than the
presence of a problem: nothing has gone wrong, the pipeline has not run yet.

Keyed by the rendered string rather than by `MeetingStatus`, because this module
is handed a `list_document` row and not a `Meeting` — and an unknown value falls
back to grey rather than raising, since a UI must never be the thing that refuses
to draw a lifecycle it has not been taught.
"""


# --- Discs: the tray, and one per meeting row -------------------------------


def _disc(painter: QPainter, size: int, color: QColor, ring: bool) -> None:
    """A filled disc, or the same disc hollowed into a ring. The degraded rule."""
    margin = size / 8
    box = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)
    if ring:
        pen = painter.pen()
        pen.setColor(color)
        pen.setWidthF(size / 6)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # Inset by half the pen width, or the stroke straddles the box edge and
        # is clipped by the pixmap at this margin.
        half = size / 12
        painter.drawEllipse(box.adjusted(half, half, -half, -half))
    else:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(box)


@lru_cache(maxsize=None)
def state_icon(state: State, degraded: bool = False) -> QIcon:
    """A filled disc in the colour of the state, hollowed into a ring when degraded."""
    return _painted(lambda p, s: _disc(p, s, QColor(COLORS[state]), degraded))


@lru_cache(maxsize=None)
def status_dot(status: str, degraded: bool = False) -> QIcon:
    """The same disc for a *meeting's* lifecycle, and the same ring for a lost channel.

    Deliberately the same two rules the tray icon has rather than a second visual
    language for the same fact: `2026-09-02_1001` was twenty-four minutes with no
    microphone in it, and a reader who has learned that a hollow icon means a run
    that lost a channel has learned it for both places at once.
    """
    return _painted(lambda p, s: _disc(p, s, QColor(LIFECYCLE.get(status, "#808080")), degraded))


# --- Glyphs: the buttons, the tabs and the queue headings -------------------


def _record(painter: QPainter, size: int) -> None:
    margin = size / 6
    painter.drawEllipse(QRectF(margin, margin, size - 2 * margin, size - 2 * margin))


def _pause(painter: QPainter, size: int) -> None:
    width, gap, height = size * 0.2, size * 0.14, size * 0.62
    top = (size - height) / 2
    radius = width / 4
    painter.drawRoundedRect(
        QRectF(size / 2 - gap / 2 - width, top, width, height), radius, radius
    )
    painter.drawRoundedRect(QRectF(size / 2 + gap / 2, top, width, height), radius, radius)


def _play(painter: QPainter, size: int) -> None:
    """Resume. The same button as pause, so it must not be the same shape."""
    painter.drawPolygon(
        QPolygonF(
            [
                QPointF(size * 0.30, size * 0.18),
                QPointF(size * 0.82, size / 2),
                QPointF(size * 0.30, size * 0.82),
            ]
        )
    )


def _stop(painter: QPainter, size: int) -> None:
    margin = size * 0.22
    painter.drawRoundedRect(
        QRectF(margin, margin, size - 2 * margin, size - 2 * margin), size / 12, size / 12
    )


def _dashboard(painter: QPainter, size: int) -> None:
    """Four panes. The one glyph here that is a picture of the page it opens."""
    margin, gap = size * 0.16, size * 0.09
    cell = (size - 2 * margin - gap) / 2
    radius = size / 20
    for x in (margin, margin + cell + gap):
        for y in (margin, margin + cell + gap):
            painter.drawRoundedRect(QRectF(x, y, cell, cell), radius, radius)


def _list(painter: QPainter, size: int) -> None:
    """Three bars of unequal length: a list of meetings, not a document."""
    margin, height, gap = size * 0.18, size * 0.115, size * 0.115
    width = size - 2 * margin
    y = margin + size * 0.04
    for fraction in (1.0, 0.72, 0.88):
        painter.drawRoundedRect(
            QRectF(margin, y, width * fraction, height), height / 2, height / 2
        )
        y += height + gap


def _tag(painter: QPainter, size: int) -> None:
    """A luggage label with its hole punched out: a project is a label, not a container."""
    margin = size * 0.16
    body = QPolygonF(
        [
            QPointF(margin, margin),
            QPointF(size * 0.62, margin),
            QPointF(size - margin, size / 2),
            QPointF(size * 0.62, size - margin),
            QPointF(margin, size - margin),
        ]
    )
    painter.drawPolygon(body)
    # Punched rather than drawn in the background colour: this pixmap is
    # transparent and is composited onto whatever the widget is, so a hole filled
    # with an assumed white would be a white dot on a dark theme.
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
    hole = size * 0.13
    painter.drawEllipse(QRectF(margin + size * 0.08, size / 2 - hole / 2, hole, hole))
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)


def _person(painter: QPainter, size: int) -> None:
    """A head and a pair of shoulders, which is as much as sixteen pixels holds."""
    head = size * 0.30
    painter.drawEllipse(QRectF(size / 2 - head / 2, size * 0.14, head, head))
    shoulders = QRectF(size * 0.18, size * 0.52, size * 0.64, size * 0.62)
    # A chord rather than an ellipse: the top half of an oval is a pair of
    # shoulders, and the bottom half would be a chin.
    painter.drawChord(shoulders, 0, 180 * 16)


def _page(painter: QPainter, size: int) -> None:
    """A sheet with two lines punched out of it: notes, as opposed to a list."""
    margin_x, margin_y = size * 0.22, size * 0.13
    painter.drawRoundedRect(
        QRectF(margin_x, margin_y, size - 2 * margin_x, size - 2 * margin_y),
        size / 16,
        size / 16,
    )
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
    height = size * 0.075
    for y in (size * 0.36, size * 0.56):
        painter.drawRoundedRect(
            QRectF(margin_x + size * 0.1, y, size * 0.36, height), height / 2, height / 2
        )
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)


def _copy(painter: QPainter, size: int) -> None:
    """Two sheets, one behind the other: the icon everything else uses for a copy.

    Deliberately the conventional shape rather than an invention. This is the one
    glyph in the set somebody has to recognise *without* reading anything beside
    it — it sits alone in the corner of the tab bar — and the whole point of a
    convention is that it is already known.

    The back sheet is separated from the front one by a cleared halo rather than
    by an outline, which is how `_page` punches its lines: the set is filled
    shapes throughout, and a stroked gap would read as an icon borrowed from
    somewhere else.
    """
    radius = size / 16
    back = QRectF(size * 0.12, size * 0.08, size * 0.54, size * 0.62)
    front = QRectF(size * 0.34, size * 0.30, size * 0.54, size * 0.62)
    painter.drawRoundedRect(back, radius, radius)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
    halo = size * 0.06
    painter.drawRoundedRect(front.adjusted(-halo, -halo, halo, halo), radius, radius)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
    painter.drawRoundedRect(front, radius, radius)


def _pulse(painter: QPainter, size: int) -> None:
    """A trace, for the page that is about what the machine is doing."""
    pen = painter.pen()
    pen.setColor(QColor(painter.brush().color()))
    # The style as well as the colour: `_fill` hands every glyph a `NoPen`, since
    # every other glyph is a filled shape, and setting a colour on a pen that is not
    # going to be stroked draws exactly nothing.
    pen.setStyle(Qt.PenStyle.SolidLine)
    pen.setWidthF(size / 9)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath(QPointF(size * 0.12, size * 0.52))
    for x, y in ((0.30, 0.52), (0.40, 0.24), (0.55, 0.78), (0.68, 0.44), (0.88, 0.44)):
        path.lineTo(QPointF(size * x, size * y))
    painter.drawPath(path)


def _check(painter: QPainter, size: int) -> None:
    """A tick, for the tab about work that gets finished.

    Stroked rather than filled, which makes it the second exception to the rule
    :data:`GLYPHS` states — for the same reason `_pulse` is the first. A tick has
    no inside; filling its outline gives a lozenge nobody reads as a tick at
    sixteen pixels. The pen setup is `_pulse`'s and is deliberately identical, so
    the two stroked glyphs have the same weight as each other rather than each
    having been tuned alone.
    """
    pen = painter.pen()
    pen.setColor(QColor(painter.brush().color()))
    pen.setStyle(Qt.PenStyle.SolidLine)
    pen.setWidthF(size / 8)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath(QPointF(size * 0.18, size * 0.54))
    path.lineTo(QPointF(size * 0.40, size * 0.76))
    path.lineTo(QPointF(size * 0.82, size * 0.26))
    painter.drawPath(path)


def _calendar(painter: QPainter, size: int) -> None:
    """A day, for the summary box and for anything that is about a date.

    Filled, like every glyph but `_pulse`: a page with two tabs on top reads as a calendar
    at this size, and the holes are punched out rather than drawn, so it stays a
    single filled shape.
    """
    body = QPainterPath()
    body.addRoundedRect(
        QRectF(size * 0.14, size * 0.22, size * 0.72, size * 0.64), size * 0.10, size * 0.10
    )
    for x in (0.30, 0.62):
        body.addRect(QRectF(size * x, size * 0.10, size * 0.08, size * 0.20))
    window = QPainterPath()
    window.addRect(QRectF(size * 0.22, size * 0.44, size * 0.56, size * 0.34))
    painter.drawPath(body.subtracted(window))


def _trash(painter: QPainter, size: int) -> None:
    """The one destructive action, and the only glyph that has to read as a warning."""
    lid = size * 0.09
    painter.drawRoundedRect(
        QRectF(size * 0.16, size * 0.22, size * 0.68, lid), lid / 2, lid / 2
    )
    handle = size * 0.07
    painter.drawRoundedRect(
        QRectF(size * 0.40, size * 0.13, size * 0.20, handle), handle / 2, handle / 2
    )
    body = QPolygonF(
        [
            QPointF(size * 0.24, size * 0.34),
            QPointF(size * 0.76, size * 0.34),
            QPointF(size * 0.70, size * 0.86),
            QPointF(size * 0.30, size * 0.86),
        ]
    )
    painter.drawPolygon(body)


GLYPHS: dict[str, Callable[[QPainter, int], None]] = {
    "record": _record,
    "pause": _pause,
    "play": _play,
    "stop": _stop,
    "dashboard": _dashboard,
    "list": _list,
    "tag": _tag,
    "person": _person,
    "page": _page,
    "pulse": _pulse,
    "trash": _trash,
    "check": _check,
    "calendar": _calendar,
    "copy": _copy,
}
"""Fourteen shapes, each drawn filled in one colour.

One style throughout — solid, no outlines, no two-tone — because a set that mixes
filled and stroked glyphs reads as icons borrowed from two places, which at this
size is the only thing anybody notices about them. `_pulse` is the exception it
has to be: a trace has no inside.
"""


@lru_cache(maxsize=None)
def glyph(name: str, color: str, size: int = ICON_SIZE) -> QIcon:
    """One glyph in one colour.

    The colour is the caller's to choose and is normally its own
    `palette().windowText().color().name()`, so the icons follow a dark theme
    instead of being drawn black onto it. It is a string rather than a `QColor`
    because this is cached on its arguments and a `QColor` does not hash.

    A theme changed while the window is open keeps the icons it has, which is the
    cost of caching them and is worth it: these are asked for on every refresh,
    once per row.
    """
    painter_color = QColor(color)
    return _painted(lambda p, s: _fill(p, s, painter_color, GLYPHS[name]), size)


def _fill(
    painter: QPainter, size: int, color: QColor, draw: Callable[[QPainter, int], None]
) -> None:
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    draw(painter, size)


def _painted(draw: Callable[[QPainter, int], None], size: int = ICON_SIZE) -> QIcon:
    """Run a drawing function over a transparent pixmap and hand back the icon."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        draw(painter, size)
    finally:
        # Qt warns loudly, and on some platforms crashes, if a QPainter is still
        # active when its paint device is destroyed.
        painter.end()
    return QIcon(pixmap)


def tooltip(state: State, missing: list[str]) -> str:
    """The hover text: the state, and what this run failed to capture."""
    text = TOOLTIPS[state]
    if missing:
        text += f" ({', '.join(missing)} not captured)"
    return text
