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

**A name is the second kind of link, and phase 5 is what gave it somewhere to
go.** A speaker label that is somebody's name, and every `[[Wikilink]]` in the
notes, becomes a `referat-person:` link the window answers by opening that
person's page. Which labels are names is **not decided here** —
:func:`referat.cli.transcript_document` says so in its `people` field, from
:func:`referat.voices.name_complaint`, which is the one function that knows `ME`
and `REMOTE` are channel labels and `SPEAKER_NN` is a number.

A wikilink is linked *whatever it says*, deliberately. `/cleanup` writes them for
people and for projects alike and nothing in the file distinguishes the two, so
the alternatives were to link none of them or to hold a list of known names in
this widget that goes stale between refreshes. The people page answers a name
nothing is filed under by saying exactly that, which is information about the
note rather than a dead end.
"""

from __future__ import annotations

import html
import logging
import re
from bisect import bisect_right
from typing import Any
from urllib.parse import quote, unquote

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QGuiApplication
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

PERSON_SCHEME = "referat-person"
"""The scheme a name links to: `referat-person:<percent-encoded name>`.

A separate scheme rather than a path under :data:`SCHEME`, so
:func:`parse_target` keeps matching a whole timestamp and neither parser has to
guess which kind of link it is holding. The name is percent-encoded because it is
whatever somebody typed into `referat label` — spaces, non-ASCII and `#` are all
allowed in a name and none of them survive a URL raw.
"""

WIKILINK_RE = re.compile(r"\[\[([^\[\]\n]+)\]\]")
"""A `[[Wikilink]]` in `notes.md`, which the cleanup prompt writes for a person.

Deliberately refuses nested brackets and a line break: a `[[` that never closes
on its own line is a typo in a note, and matching across lines would swallow a
paragraph.
"""


def _anchor(seconds: float) -> str:
    """The anchor name for an entry starting at `seconds`. Whole seconds, as rendered."""
    return f"t{int(seconds)}"


def person_url(name: str) -> str:
    """The link a name becomes. Percent-encoded, because a name is somebody's typing."""
    return f"{PERSON_SCHEME}:{quote(name, safe='')}"


def parse_person(url: QUrl) -> str | None:
    """The name a `referat-person:` link points at, or None for anything else.

    `QUrl.scheme` lowercases, which is right here — the scheme is ours and fixed —
    while the name after it is taken from the raw string and decoded, so its own
    case survives. Reading it back through `QUrl.path` would let Qt normalise a
    name, and a name is compared against the database exactly as it was filed.
    """
    if url.scheme() != PERSON_SCHEME:
        return None
    return unquote(url.toString()[len(PERSON_SCHEME) + 1 :]).strip() or None


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

    A label that is a **name** is also a link to that person, and which labels
    those are comes from the document's `people` — never from a rule spelled out
    here. `ME`, `REMOTE` and every `SPEAKER_NN` are drawn exactly as they were.
    """
    style = (
        "<style>"
        "p.e { margin: 0 0 6px 0; }"
        "span.ts { color: #909090; font-family: monospace; }"
        "span.lb { font-weight: bold; }"
        "a.lb { font-weight: bold; text-decoration: none; }"
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
    named = set(document.get("people", ()))
    body = [
        f'<p class="e" id="{_anchor(entry["at"])}">'
        f'<a name="{_anchor(entry["at"])}"></a>'
        f'<span class="ts">[{html.escape(entry["time"])}]</span> '
        f'{_label_html(entry["label"], named)} '
        f'{html.escape(entry["text"])}</p>'
        for entry in entries
    ]
    return style + head + "".join(body)


def _label_html(label: str, named: set[str]) -> str:
    """The speaker cell: a link when the label is a person, plain bold otherwise."""
    text = html.escape(label)
    if label not in named:
        return f'<span class="lb">{text}:</span>'
    return f'<a class="lb" href="{html.escape(person_url(label), quote=True)}">{text}</a>:'


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


def link_people(markdown: str) -> str:
    """Rewrite every `[[Wikilink]]` in the notes into a link to that person.

    The brackets are escaped back in, as :func:`link_timestamps` keeps its own:
    a `[[Wikilink]]` is how `/cleanup` spells a person and a link that quietly
    dropped the brackets would look like a different notation. They also mean the
    two rewrites cannot collide — a timestamp has no `[[` and this leaves
    `[00:12:30]` alone.

    Every wikilink is linked, including one naming a project or a topic. Nothing
    in a note distinguishes those from a person, and the people page answers a
    name nothing is filed under by saying so — see the module docstring.
    """

    def link(match: re.Match[str]) -> str:
        name = match.group(1).strip()
        if not name:
            return match.group(0)
        return rf"[\[\[{name}\]\]](<{person_url(name)}>)"

    return WIKILINK_RE.sub(link, markdown)


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
    """A link that is neither a timestamp nor a name. The window decides what to do."""

    person_requested = Signal(str)
    """A speaker label or a `[[Wikilink]]` was clicked. The window opens that person.

    A name rather than a URL, because the receiver looks it up by name and this
    widget is the only thing that should know a name was ever percent-encoded.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.transcript = self._browser()
        self.notes = self._browser()
        # Notes first, and that ordering is a claim about which document is the
        # point. The transcript is evidence and a source; the notes are what
        # somebody actually reads, and putting the transcript in front made every
        # meeting open on several hundred utterances.
        self.addTab(self.notes, "Notes")
        self.addTab(self.transcript, "Transcript")
        self._offsets: list[float] = []
        self._raw: dict[QTextBrowser, str] = {}
        """The exact bytes behind each pane, for a copy that is not a rendering."""
        self._zoom = 0
        self.clear_meeting("Select a meeting.")

    # --- Zoom ---------------------------------------------------------------

    def zoom(self, steps: int) -> None:
        """Grow or shrink both panes together, or reset them when `steps` is 0.

        Both, deliberately: they are two tabs of one document and a reader who
        made the notes bigger did not ask for the transcript to stay small.

        Qt's own Ctrl+wheel already worked and is not enough — it needs a mouse,
        and it moves one pane. Tracked as a running total so a reset is exact
        rather than a guess at how many steps to undo.
        """
        if steps == 0:
            for browser in (self.transcript, self.notes):
                browser.zoomOut(self._zoom) if self._zoom > 0 else browser.zoomIn(-self._zoom)
            self._zoom = 0
            return
        self._zoom += steps
        for browser in (self.transcript, self.notes):
            browser.zoomIn(steps) if steps > 0 else browser.zoomOut(-steps)

    # --- Copying ------------------------------------------------------------

    def copy_current(self) -> str:
        """Put the visible pane on the clipboard as plain text, and say what went.

        **The raw file, not the rendering.** The notes pane holds Markdown that
        has been through `setMarkdown` and had its timestamps and wikilinks
        rewritten into `referat:` links; copying *that* back out would paste
        somebody a document full of link syntax pointing at a scheme only this
        window answers. So the original text is kept when the pane is filled and
        handed over verbatim.

        Plain text only, which is the other half of the complaint this answers:
        Qt puts an HTML flavour on the clipboard beside the text, and Word,
        Google Docs and Outlook all prefer it — so a paste arrived as
        theme-coloured monospace. `setText` writes one flavour and there is
        nothing for them to prefer. Same rule the meetings folder's
        `editor.copyWithSyntaxHighlighting: false` sets for VS Code.

        A selection wins over the whole document when there is one, because
        somebody who selected three lines meant three lines.
        """
        browser = self.currentWidget()
        if not isinstance(browser, QTextBrowser):
            return ""
        cursor = browser.textCursor()
        if cursor.hasSelection():
            text = cursor.selection().toPlainText()
            what = "the selection"
        else:
            text = self._raw.get(browser) or browser.toPlainText()
            what = self.tabText(self.currentIndex()).lower()
        text = text.strip()
        if text:
            QGuiApplication.clipboard().setText(text)
        return what if text else ""

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
        self._raw = {}
        for browser in (self.transcript, self.notes):
            browser.setHtml(f'<p style="color: #909090">{html.escape(message)}</p>')

    def show_working(self, title: str, lines: list[str]) -> None:
        """A meeting that is being recorded or transcribed right now.

        The complaint this answers is that such a meeting drew two empty panes,
        which is what "no transcript.md yet" looks like and also what a broken
        window looks like. A transcript genuinely does not exist until the
        pipeline writes it at the very end — it is written whole and atomically,
        not streamed — so there is nothing to show and the honest thing is to say
        which of the two is true and what happens next.
        """
        self._offsets = []
        self._raw = {}
        body = "".join(f"<p>{html.escape(line)}</p>" for line in lines)
        for browser in (self.transcript, self.notes):
            browser.setHtml(
                f'<p style="font-weight: bold">{html.escape(title)}</p>'
                f'<div style="color: #909090">{body}</div>'
            )

    def show_meeting(self, document: dict[str, Any], notes: str | None) -> None:
        """Render one meeting: the parsed transcript, and its notes if it has any.

        `notes` is the raw `notes.md`, or None when the meeting has none — which
        is a thing the tab says out loud rather than showing as an empty pane. A
        meeting only gets notes when somebody runs `/cleanup` on it, and an empty
        pane looks like a failure rather than like a queue.
        """
        self.transcript.setHtml(render_transcript_html(document))
        self._offsets = [float(entry["at"]) for entry in document["entries"]]
        # Kept for `copy_current`, which hands over the source rather than the
        # rendering. The transcript's plain text is its own rendering and is
        # already clean -- `[HH:MM:SS] Name: text` -- so only the notes need it.
        self._raw = {self.notes: notes} if notes is not None else {}
        if notes is None:
            self.notes.setHtml(
                '<p style="color: #909090">No notes.md yet. Notes are written by '
                "the <code>/cleanup</code> slash command in the meetings folder, "
                "never by Referat itself.</p>"
            )
        else:
            # Both rewrites, and they cannot collide: a timestamp has no `[[` and a
            # wikilink has no `[HH:MM:SS]` inside it.
            self.notes.setMarkdown(link_people(link_timestamps(notes)))
        self.transcript.moveCursor(self.transcript.textCursor().MoveOperation.Start)

    # --- Cross-links --------------------------------------------------------

    def _on_anchor(self, url: QUrl) -> None:
        """Three kinds of link: a timestamp scrolls, a name is emitted, the rest leave.

        Ordered narrowest first, and the fallthrough is unchanged: anything this
        widget does not recognise is still the window's to decide about, which is
        what keeps an `http://` in somebody's notes working.
        """
        target = parse_target(url)
        if target is not None:
            self.goto(target)
            return
        name = parse_person(url)
        if name is not None:
            self.person_requested.emit(name)
            return
        self.external_requested.emit(url)

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
