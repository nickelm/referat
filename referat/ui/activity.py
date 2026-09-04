"""What Referat is doing and what it is about to do.

The status bar's activity strip answers *is something running* in one line, from
every tab. This page is the other half of that question — **what is queued
behind it, and what has the machine actually been doing** — and it exists
because the honest answer to "why has nothing happened for two minutes" used to
be "open the log file in an editor".

Two panes, and they are two different kinds of truth:

- **The queue** is live state, out of :mod:`referat.progress`: what is running
  now, what is waiting, how far in. It is the same document the status strip
  renders, shown as a list rather than as one line.
- **The log** is history, and it is the real rotating log file rather than a
  second record kept in memory. Referat already writes everything worth knowing
  there — every state transition, every model load, every gate verdict, every
  refusal — and a UI keeping its own parallel history would be a worse copy of
  it that disagrees the first time something is logged from a thread this page
  never hears about.

**This is the one page with a timer**, which is a deliberate exception to the
window's *refresh on show, on F5, on a transition* rule. Everything else here
redraws because something happened; a log file grows with no event this process
can see, and a tail that only updated when you switched tabs would be a tail of
the past. It runs **only while this page is in front** and costs a read of the
end of one file.
"""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from referat import paths
from referat.ui import lists

log = logging.getLogger(__name__)

TAIL_BYTES = 200_000
"""How much of the end of the log to read. Enough for a long transcription's
worth of lines, small enough that reading it every two seconds is free."""

REFRESH_MS = 2000

IDLE = "Nothing running. Transcription starts when a recording stops; notes run when you ask."

NOISE = ("httpx", "urllib3", "huggingface", "filelock", "faster_whisper")
"""Library chatter that is not Referat saying anything about the work.

`httpx` in particular is why somebody thought the Whisper model was being
downloaded on every run: it logs one `GET .../revision/main` line per load,
which is a metadata check against a model that is already cached on disk.
"""


class ActivityPage(QWidget):
    """The queue on top, the log beneath, refreshed while it is being looked at."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._log_path = paths.log_path()
        self._follow = True

        self.jobs = QTreeWidget()
        self.jobs.setColumnCount(4)
        self.jobs.setHeaderLabels(["Job", "Meeting", "Doing", "Progress"])
        self.jobs.setRootIsDecorated(False)
        self.jobs.setUniformRowHeights(True)
        lists.stripe(self.jobs)
        self.jobs.setMaximumHeight(160)

        self.summary = QLabel(IDLE)
        self.summary.setWordWrap(True)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        # A monospace pane, because these are aligned timestamped records and
        # proportional text turns a scannable column into prose.
        self.log.setStyleSheet("font-family: Consolas, monospace;")

        self.quiet = QCheckBox("Referat only")
        self.quiet.setChecked(True)
        self.quiet.setToolTip(
            "Hide library chatter - httpx, huggingface, faster_whisper. The httpx "
            "line is a cached-model revision check, not a download."
        )
        self.quiet.toggled.connect(self._fill_log)

        self.following = QCheckBox("Follow")
        self.following.setChecked(True)
        self.following.setToolTip("Scroll to the newest line as it arrives.")

        self.open_button = QPushButton("Open log file")
        self.open_button.setAutoDefault(False)
        self.open_button.clicked.connect(self._on_open)

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.addWidget(QLabel("Log"))
        controls.addWidget(self.quiet)
        controls.addWidget(self.following)
        controls.addStretch(1)
        controls.addWidget(self.open_button)

        top = QVBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(QLabel("Running and queued"))
        top.addWidget(self.jobs, 1)
        top.addWidget(self.summary)
        top_pane = QWidget()
        top_pane.setLayout(top)

        bottom = QVBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.addLayout(controls)
        bottom.addWidget(self.log, 1)
        bottom_pane = QWidget()
        bottom_pane.setLayout(bottom)

        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(top_pane)
        split.addWidget(bottom_pane)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 4)
        split.setSizes([180, 520])

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(split)
        self.setLayout(layout)

        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self._fill_log)

    # --- Being looked at ------------------------------------------------------

    def refresh(self) -> None:
        """Called like every other page's `refresh`, plus it arms the tail."""
        self._fill_log()

    def showEvent(self, event) -> None:  # noqa: N802  (Qt's own spelling)
        super().showEvent(event)
        self._timer.start()
        self._fill_log()

    def hideEvent(self, event) -> None:  # noqa: N802
        """Stop the timer the moment this page is not in front.

        The window's whole refresh discipline is that nothing costs anything when
        nobody is looking, and a timer left running behind another tab is exactly
        the thing that rule exists to prevent.
        """
        super().hideEvent(event)
        self._timer.stop()

    # --- The queue ------------------------------------------------------------

    def on_progress(self, jobs: list[Any]) -> None:
        """Render the running and queued jobs. **On the GUI thread only.**

        Fed by the window, which is fed by the shell's `Bridge`. This page never
        registers with :mod:`referat.progress` itself: a second listener would be
        a second thing to marshal, and the window already has one.
        """
        self.jobs.clear()
        for job in jobs:
            if job.fraction is None:
                measure = "queued" if job.phase == "queued" else "working"
            else:
                measure = f"{job.fraction * 100:.0f}%"
            item = QTreeWidgetItem([job.kind, job.title, job.phase, measure])
            item.setTextAlignment(3, Qt.AlignmentFlag.AlignRight)
            self.jobs.addTopLevelItem(item)
        for column in range(4):
            self.jobs.resizeColumnToContents(column)
        if not jobs:
            self.summary.setText(IDLE)
            return
        running = sum(1 for j in jobs if j.phase != "queued")
        waiting = len(jobs) - running
        parts = [f"{running} running"]
        if waiting:
            parts.append(f"{waiting} queued")
        self.summary.setText(", ".join(parts) + ".")

    # --- The log --------------------------------------------------------------

    def _fill_log(self) -> None:
        """Re-read the tail of the rotating log and show it.

        The whole visible text is replaced rather than appended to, which is the
        simple thing and is correct across a rotation — an append would need to
        track a file offset that a rollover invalidates, and would show the same
        lines twice the first time it happened.

        The scroll position is kept unless *Follow* is on, so reading something
        five minutes old is not interrupted every two seconds.
        """
        try:
            with self._log_path.open("rb") as handle:
                handle.seek(0, 2)
                handle.seek(max(0, handle.tell() - TAIL_BYTES))
                raw = handle.read().decode("utf-8", errors="replace")
        except OSError:
            self.log.setPlainText(f"No log at {self._log_path} yet.")
            return

        lines = raw.split("\n")[1:]  # the first is normally a partial line
        if self.quiet.isChecked():
            lines = [line for line in lines if not any(n in line for n in NOISE)]

        bar = self.log.verticalScrollBar()
        at_bottom = self.following.isChecked() or bar.value() >= bar.maximum() - 4
        position = bar.value()
        self.log.setPlainText("\n".join(lines).strip())
        bar.setValue(bar.maximum() if at_bottom else min(position, bar.maximum()))

    def _on_open(self) -> None:
        """Hand the log to the desktop, for when the tail is not enough."""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._log_path))):
            log.warning("could not open %s", self._log_path)
