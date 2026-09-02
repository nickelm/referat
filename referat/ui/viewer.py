"""One meeting's documents: `transcript.md` beside `notes.md`, in two tabs.

**The transcript is rendered from the parsed entries, not from the Markdown.**
:func:`referat.cli.transcript_document` hands over `(at, time, label, text)` and
this builds the HTML, which is what makes a timestamp addressable: every entry
gets an anchor, so a `[HH:MM:SS]` in the notes can navigate to it. Rendering the
raw Markdown through `setMarkdown` would be one line and would leave nothing to
scroll to.

**The notes are the opposite and go through `setMarkdown`**, because they are
somebody's prose with headings, bullets and links in it and Qt's own Markdown
reader renders all of that. The one thing done to them first is rewriting each
`[HH:MM:SS]` into a link this widget answers.

**A timestamp is audio-elapsed with pauses excluded, and it is not a seek.** By
the time anybody reads a transcript the WAVs are normally deleted, so a
cross-link is a scroll position and nothing more. That is why the target is
found by *nearest entry at or before* rather than by exact match: the notes are
written by a person or by `/cleanup` and their timestamps are approximate, while
the transcript's are whole seconds of a real segment start.
"""

from __future__ import annotations

import html
import logging
import re
from bisect import bisect_right
from typing import Any

from PySide6.QtCore import QUrl, Signal
from PySide6.QtWidgets import QTabWidget, QTextBrowser, QWidget

log = logging.getLogger(__name__)

TIMESTAMP_RE = re.compile(r"\[(\d{2}):(\d{2}):(\d{2})\]")
"""A `[HH:MM:SS]` reference in `notes.md`, which is what becomes a cross-link.

Deliberately looser than :data:`referat.transcribe.ENTRY_RE`, and it has to be:
that one matches a whole rendered entry, and a note says things like *(around
[00:12:30] they agreed to …)* where the timestamp stands on its own.
"""

SCHEME = "referat"
"""The URL scheme this widget answers, so nothing here ever opens a browser.

Both browsers run with `setOpenLinks(False)`: a `referat:` link is handled here,
and anything else — an `http://` a note happens to carry — is handed to
:data:`Viewer.external_requested` for the window to decide about. A QTextBrowser
left to its own devices would try to *load* an unknown URL into itself and
blank the pane.
"""


def _anchor(seconds: float) -> str:
    """The anchor name for an entry starting at `seconds`. Whole seconds, as rendered."""
    return f"t{int(seconds)}"


def render_transcript_html(document: dict[str, Any]) -> str:
    """The transcript pane's HTML, from :func:`referat.cli.transcript_document`.

    Escaped with :func:`html.escape` entry by entry, and that is not a formality:
    the text is whatever Whisper heard in a meeting and the label is whatever
    somebody typed into `referat label`, so an `<` in either is data. This is the
    same rule `referat-vscode/media/sidebar.js` follows by building every node
    with `textContent`.

    Colours are left to two grays that read on a light and a dark palette alike,
    rather than pulled from the theme: the pane is a `QTextBrowser` whose own
    palette already paints the background and the body text, and the only two
    things this stylesheet says are *the timestamp is quieter than the speech*
    and *the label is not*.
    """
    style = (
        "<style>"
        "p.e { margin: 0 0 6px 0; }"
        "span.ts { color: #909090; font-family: monospace; }"
        "span.lb { font-weight: bold; }"
        "p.hd { margin: 0 0 12px 0; font-weight: bold; }"
        "p.no { margin: 0; color: #909090; }"
        "</style>"
    )
    title = html.escape(document["title"])
    head = f'<p class="hd">{title} &mdash; {html.escape(document["duration"])}</p>'
    if not document["exists"]:
        return f'{style}{head}<p class="no">No transcript.md in this meeting folder.</p>'
    entries = document["entries"]
    if not entries:
        return (
            f'{style}{head}<p class="no">transcript.md holds no entry this '
            f"parser recognises.</p>"
        )
    body = [
        f'<p class="e" id="{_anchor(entry["at"])}">'
        f'<a name="{_anchor(entry["at"])}"></a>'
        f'<span class="ts">[{html.escape(entry["time"])}]</span> '
        f'<span class="lb">{html.escape(entry["label"])}:</span> '
        f'{html.escape(entry["text"])}</p>'
        for entry in entries
    ]
    return style + head + "".join(body)


def link_timestamps(markdown: str) -> str:
    """Rewrite every `[HH:MM:SS]` in the notes into a link the transcript answers.

    The brackets are escaped back in so the rendered text still reads
    `[00:12:30]`: they are how a timestamp is spelled everywhere else in Referat,
    and a link that quietly dropped them would look like a different notation.

    The destination is wrapped in angle brackets, which is CommonMark's form for
    a destination that may hold anything — `referat:00:12:30` has two colons in
    it and a bare destination is not required to survive them.
    """

    def link(match: re.Match[str]) -> str:
        clock = match.group(0)[1:-1]
        return rf"[\[{clock}\]](<{SCHEME}:{clock}>)"

    return TIMESTAMP_RE.sub(link, markdown)


CLOCK_RE = re.compile(r"(\d{2}):(\d{2}):(\d{2})")


def parse_target(url: QUrl) -> float | None:
    """The seconds a `referat:HH:MM:SS` link points at, or None for anything else."""
    if url.scheme() != SCHEME:
        return None
    match = CLOCK_RE.fullmatch(url.toString()[len(SCHEME) + 1 :])
    if match is None:
        return None
    hours, minutes, seconds = (int(part) for part in match.groups())
    return float(hours * 3600 + minutes * 60 + seconds)


class Viewer(QTabWidget):
    """The transcript and the notes for one meeting, and the links between them."""

    external_requested = Signal(QUrl)
    """A link that is not a `referat:` timestamp. The window decides what to do."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.transcript = self._browser()
        self.notes = self._browser()
        self.addTab(self.transcript, "Transcript")
        self.addTab(self.notes, "Notes")
        self._offsets: list[float] = []
        self.clear_meeting("Select a meeting.")

    def _browser(self) -> QTextBrowser:
        browser = QTextBrowser(self)
        # Both off, or QTextBrowser answers a click itself: an unknown scheme by
        # trying to load it into the pane, an http one by handing it to the
        # desktop with no say from here.
        browser.setOpenLinks(False)
        browser.setOpenExternalLinks(False)
        browser.anchorClicked.connect(self._on_anchor)
        return browser

    # --- Filling it ---------------------------------------------------------

    def clear_meeting(self, message: str) -> None:
        """Both panes say the same thing: there is nothing selected, or nothing there."""
        self._offsets = []
        for browser in (self.transcript, self.notes):
            browser.setHtml(f'<p style="color: #909090">{html.escape(message)}</p>')

    def show_meeting(self, document: dict[str, Any], notes: str | None) -> None:
        """Render one meeting: the parsed transcript, and its notes if it has any.

        `notes` is the raw `notes.md`, or None when the meeting has none — which
        is a thing the tab says out loud rather than showing as an empty pane. A
        meeting only gets notes when somebody runs `/cleanup` on it, and an empty
        pane looks like a failure rather than like a queue.
        """
        self.transcript.setHtml(render_transcript_html(document))
        self._offsets = [float(entry["at"]) for entry in document["entries"]]
        if notes is None:
            self.notes.setHtml(
                '<p style="color: #909090">No notes.md yet. Notes are written by '
                "the <code>/cleanup</code> slash command in the meetings folder, "
                "never by Referat itself.</p>"
            )
        else:
            self.notes.setMarkdown(link_timestamps(notes))
        self.transcript.moveCursor(self.transcript.textCursor().MoveOperation.Start)

    # --- Cross-links --------------------------------------------------------

    def _on_anchor(self, url: QUrl) -> None:
        target = parse_target(url)
        if target is None:
            self.external_requested.emit(url)
            return
        self.goto(target)

    def goto(self, seconds: float) -> None:
        """Show the transcript tab, scrolled to the entry covering `seconds`.

        *Covering*, not *at*: the nearest entry starting at or before the target.
        A note's timestamp is approximate and an exact hit would usually miss,
        leaving a click that visibly did nothing.
        """
        if not self._offsets:
            return
        position = bisect_right(self._offsets, seconds) - 1
        at = self._offsets[max(0, position)]
        self.setCurrentWidget(self.transcript)
        self.transcript.scrollToAnchor(_anchor(at))
