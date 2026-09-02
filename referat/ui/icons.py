"""The notification-area icon, one per recorder state, drawn with QPainter.

Replaces the Pillow drawing that lived in :mod:`referat.tray` until step 20.
Same two rules it had: the *colour* carries the state, and the *shape* carries a
run that lost a channel — a ring rather than a disc, because a ring reads as
wrong at sixteen pixels in a way that a slightly different red does not, and
because for a meeting held in a room a lost microphone means nothing is being
recorded at all.

Pure Qt, so it holds to the portability rule in :mod:`referat.ui`. Icons are
built once per process and cached: `QSystemTrayIcon.setIcon` is called on every
transition, and repainting five pixmaps each time would be work for nothing.
"""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

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


@lru_cache(maxsize=None)
def state_icon(state: State, degraded: bool = False) -> QIcon:
    """A filled disc in the colour of the state, hollowed into a ring when degraded."""
    pixmap = QPixmap(ICON_SIZE, ICON_SIZE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        color = QColor(COLORS[state])
        margin = ICON_SIZE / 8
        box = QRectF(margin, margin, ICON_SIZE - 2 * margin, ICON_SIZE - 2 * margin)
        if degraded:
            pen = painter.pen()
            pen.setColor(color)
            pen.setWidthF(ICON_SIZE / 6)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            # Inset by half the pen width, or the stroke straddles the box edge
            # and is clipped by the pixmap at this margin.
            half = ICON_SIZE / 12
            painter.drawEllipse(box.adjusted(half, half, -half, -half))
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawEllipse(box)
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
