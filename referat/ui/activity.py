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

**The tail may not cost a selection.** Until 2026-10-12 every tick replaced the
whole pane whether or not the file had grown, and replacing a document resets
its cursor — so a drag across the last error was wiped within two seconds and
the only selection fast enough to survive was *Select all*. The pane is now
left alone when nothing changed and while anything in it is selected, and says
that it is holding.

**And what gets copied out of a log is a record, not a line.** An error here is
a header and a traceback, so :func:`entries` splits the tail where the
formatter starts a record and *Copy this entry* and *Copy last error* hand over
the whole of one.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QMenu,
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

ENTRY_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} (\w+)")
"""Where a record starts: `logging_setup.FORMAT`'s timestamp, then the level.

Every line that does not match belongs to the record above it, which is what
keeps a traceback with the message it explains.
"""

PROBLEMS = ("WARNING", "ERROR", "CRITICAL")

LOG = "Log"
HELD = "Log (holding still while text is selected)"
FLASH_MS = 4000


def entries(text: str) -> list[str]:
    """The log split into records, each a header line and whatever follows it.

    Lines ahead of the first header are the end of a record the tail cut into;
    they come back as a record of their own rather than being dropped, so the
    records always join back into exactly the text they came from.
    """
    out: list[list[str]] = []
    for line in text.split("\n"):
        if ENTRY_RE.match(line) or not out:
            out.append([line])
        else:
            out[-1].append(line)
    return ["\n".join(record) for record in out]


def last_problem(records: list[str]) -> str:
    """The most recent warning or error, traceback included, or `""`."""
    for record in reversed(records):
        match = ENTRY_RE.match(record)
        if match is not None and match.group(1) in PROBLEMS:
            return record.rstrip()
    return ""


def entry_at(text: str, line: int) -> str:
    """The record that line number `line` of `text` belongs to."""
    seen = 0
    for record in entries(text):
        seen += record.count("\n") + 1
        if line < seen:
            return record.rstrip()
    return ""


class ActivityPage(QWidget):
    """The queue on top, the log beneath, refreshed while it is being looked at."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._log_path = paths.log_path()
        self._follow = True
        self._tail = ""
        """The tail as read, before *Referat only* narrows it. What a copy reads."""
        self._shown: str | None = None
        """The text last put in the pane, so an unchanged tail is not redrawn."""
        self._flashing = False

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
        self.log.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.log.customContextMenuRequested.connect(self._on_menu)

        self.quiet = QCheckBox("Referat only")
        self.quiet.setChecked(True)
        self.quiet.setToolTip(
            "Hide library chatter - httpx, huggingface, faster_whisper. The httpx "
            "line is a cached-model revision check, not a download."
        )
        # Forced: somebody changing what the pane shows has asked for a redraw,
        # selection or not.
        self.quiet.toggled.connect(lambda _checked: self._fill_log(force=True))

        self.following = QCheckBox("Follow")
        self.following.setChecked(True)
        self.following.setToolTip("Scroll to the newest line as it arrives.")

        self.copy_error_button = QPushButton("Copy last error")
        self.copy_error_button.setToolTip(
            "The most recent warning or error, with its traceback, onto the clipboard."
        )
        self.copy_error_button.setAutoDefault(False)
        self.copy_error_button.setEnabled(False)
        self.copy_error_button.clicked.connect(self._on_copy_error)

        self.open_button = QPushButton("Open log file")
        self.open_button.setAutoDefault(False)
        self.open_button.clicked.connect(self._on_open)

        self.log_label = QLabel(LOG)

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.addWidget(self.log_label)
        controls.addWidget(self.quiet)
        controls.addWidget(self.following)
        controls.addStretch(1)
        controls.addWidget(self.copy_error_button)
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

    def _fill_log(self, force: bool = False) -> None:
        """Re-read the tail of the rotating log and show it.

        The whole visible text is replaced rather than appended to, which is the
        simple thing and is correct across a rotation — an append would need to
        track a file offset that a rollover invalidates, and would show the same
        lines twice the first time it happened.

        **Replaced only when it changed, and never under a selection** unless
        `force` says somebody asked. Replacing a document resets its cursor, so
        a redraw every two seconds is a selection that lasts two seconds. The
        file is still *read* every tick: what *Copy last error* hands over is
        the tail on disk, not whatever the held pane happens to show.

        The scroll position is kept unless *Follow* is on, so reading something
        five minutes old is not interrupted every two seconds.
        """
        try:
            with self._log_path.open("rb") as handle:
                handle.seek(0, 2)
                handle.seek(max(0, handle.tell() - TAIL_BYTES))
                raw = handle.read().decode("utf-8", errors="replace")
        except OSError:
            self._tail = ""
            self.copy_error_button.setEnabled(False)
            self._shown = None
            self.log.setPlainText(f"No log at {self._log_path} yet.")
            return

        # The first line is normally a partial one.
        self._tail = raw.replace("\r\n", "\n").split("\n", 1)[-1]
        records = entries(self._tail)
        self.copy_error_button.setEnabled(bool(last_problem(records)))
        if self.quiet.isChecked():
            # Judged on a record's header and applied to the whole record: a
            # traceback of Referat's own that passes through a library's frames
            # names that library on every other line, and filtering by line
            # dropped those lines out of the middle of it.
            records = [
                record
                for record in records
                if not any(n in record.split("\n", 1)[0] for n in NOISE)
            ]
        text = "\n".join(records).strip()

        held = not force and self._selecting()
        if not self._flashing:
            self.log_label.setText(HELD if held else LOG)
        if held or (text == self._shown and not force):
            return

        bar = self.log.verticalScrollBar()
        at_bottom = self.following.isChecked() or bar.value() >= bar.maximum() - 4
        position = bar.value()
        self.log.setPlainText(text)
        self._shown = text
        bar.setValue(bar.maximum() if at_bottom else min(position, bar.maximum()))

    def _selecting(self) -> bool:
        """Whether a redraw now would take something out of somebody's hands.

        A selection that exists, or one being started: a press has no selection
        yet, and a redraw between the press and the first movement would move
        the anchor the drag is measured from.
        """
        if self.log.textCursor().hasSelection():
            return True
        pressed = QApplication.mouseButtons() != Qt.MouseButton.NoButton
        return pressed and self.log.underMouse()

    # --- Copying out of it ----------------------------------------------------

    def _on_menu(self, where: QPoint) -> None:
        """Right-click: the selection, the record under the pointer, or the last error."""
        line = self.log.cursorForPosition(where).blockNumber()
        entry = entry_at(self.log.toPlainText(), line)
        problem = last_problem(entries(self._tail))

        menu = QMenu(self.log)
        copy = menu.addAction("Copy")
        copy.setEnabled(self.log.textCursor().hasSelection())
        copy.triggered.connect(lambda _checked=False: self.log.copy())
        this = menu.addAction("Copy this entry")
        this.setEnabled(bool(entry))
        this.triggered.connect(lambda _checked=False: self._copy(entry, "that entry"))
        error = menu.addAction("Copy last error")
        error.setEnabled(bool(problem))
        error.triggered.connect(lambda _checked=False: self._on_copy_error())
        menu.addSeparator()
        menu.addAction("Select all").triggered.connect(
            lambda _checked=False: self.log.selectAll()
        )
        menu.exec(self.log.viewport().mapToGlobal(where))

    def _on_copy_error(self) -> None:
        """The newest warning or error in the tail, whatever the filter is hiding."""
        self._copy(last_problem(entries(self._tail)), "the last error")

    def _copy(self, text: str, what: str) -> None:
        """Put one record on the clipboard and say so where the eye already is."""
        if not text:
            return
        QGuiApplication.clipboard().setText(text)
        count = text.count("\n") + 1
        self._flashing = True
        self.log_label.setText(
            f"Log - copied {what} ({count} line{'s' if count != 1 else ''})"
        )
        QTimer.singleShot(FLASH_MS, self._end_flash)

    def _end_flash(self) -> None:
        self._flashing = False
        self.log_label.setText(HELD if self._selecting() else LOG)

    def _on_open(self) -> None:
        """Hand the log to the desktop, for when the tail is not enough."""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._log_path))):
            log.warning("could not open %s", self._log_path)
