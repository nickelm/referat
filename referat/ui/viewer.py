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

**Getting text out of here is two commands and one pipeline.** A note leaves this
window for Claude or for a Google Doc, and those want different things: Markdown
source, or formatted text. So whatever is being copied becomes **Markdown
first**, and the only difference between the two commands is which clipboard
flavour that Markdown is written as — plain text alone, or clean HTML from
:mod:`referat.ui.richtext` with the Markdown beside it as the fallback flavour.

Two commands and not one clipboard carrying both, which is the measured part:
Word, Google Docs and Outlook all *prefer* an HTML flavour when one is there, so
a single copy offering both would decide for the paste. Offering them by name
leaves that choice where it belongs.

**What is copied is the source, selection included.** `setMarkdown` keeps no map
from the document it built back to the text it was given, so the map is built:
:func:`referat.ui.richtext.blocks` splits a source exactly where Qt splits it
into `QTextBlock`s, and :meth:`Viewer._source_selection` turns the selected
blocks back into the Markdown they were written as. It refuses when the two
counts disagree rather than guessing, and then Qt's own
`QTextDocumentFragment.toMarkdown` reconstructs the fragment instead, with
:func:`unlink` taking the `referat:` and `referat-person:` links back out — an
internal scheme must never leave this window.

That order is measured rather than tidy: **Qt's Markdown writer drops `**bold**`
and `*italic*` altogether** on the way out of a document, and the notes put every
action item's owner in bold. So the reconstruction is the last resort and not the
plan.
"""

from __future__ import annotations

import html
import logging
import re
from bisect import bisect_left, bisect_right
from typing import Any
from urllib.parse import quote, unquote

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QMenu, QTabWidget, QTextBrowser, QToolButton, QWidget

from referat.ui import icons, richtext

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

MEETING_SCHEME = "referat-meeting"
"""The scheme a meeting id links to: `referat-meeting:<id>`.

The third and last of them, and it arrived with the day summary, whose bullets
cite the meeting each came from. An id is `YYYY-MM-DD_HHMM` with an optional
`_2` — no space, no non-ASCII and nothing a URL objects to — so unlike a person's
name it is carried raw, and :func:`parse_meeting` still checks the shape rather
than trusting whatever a document put after the colon.
"""

WIKILINK_RE = re.compile(r"\[\[([^\[\]\n]+)\]\]")
"""A `[[Wikilink]]` in `notes.md`, which the cleanup prompt writes for a person.

Deliberately refuses nested brackets and a line break: a `[[` that never closes
on its own line is a typo in a note, and matching across lines would swallow a
paragraph.
"""

MEETING_ID_RE = re.compile(r"\[(\d{4}-\d{2}-\d{2}_\d{4}(?:_\d+)?)\]")
"""A `[2026-09-03_1408]` citation, which is how a day summary says where a line
came from.

Single brackets, the notation `[HH:MM:SS]` already uses in the notes, and it
cannot collide with one: a timestamp has two colons and no underscore. The
optional `_2` is the collision suffix `paths` gives two meetings that started in
the same minute.
"""

MARKDOWN_ESCAPE_RE = re.compile(r"([\\*_`])")
"""What has to be escaped to put arbitrary text inside Markdown emphasis.

Only reached for a person's name and only since the brackets came off: a
wikilink's text is now wrapped in `**`, so a name holding an asterisk would end
the bold halfway through itself. The name in the *link* is untouched by this —
it is percent-encoded there and is what the people page is asked for.
"""


def _iterate_blocks(document: Any) -> Any:
    """Every `QTextBlock` of a document, in order. Qt exposes no iterator for this."""
    block = document.begin()
    while block.isValid():
        yield block
        block = block.next()


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


def meeting_url(meeting_id: str) -> str:
    """The link a meeting citation becomes."""
    return f"{MEETING_SCHEME}:{meeting_id}"


def parse_meeting(url: QUrl) -> str | None:
    """The meeting id a `referat-meeting:` link points at, or None for anything else.

    The shape is checked here rather than taken on trust, the way
    :func:`parse_target` checks a clock: what reaches this is whatever a document
    put between brackets, and an id that is not an id would be handed to
    `resolve_meeting` to refuse one layer further out.
    """
    if url.scheme() != MEETING_SCHEME:
        return None
    rest = unquote(url.toString()[len(MEETING_SCHEME) + 1 :]).strip()
    return rest if MEETING_ID_RE.fullmatch(f"[{rest}]") else None


def render_transcript_html(document: dict[str, Any]) -> str:
    """The transcript pane's HTML, from :func:`referat.cli.transcript_document`.

    Escaped with :func:`html.escape` entry by entry, and that is not a formality:
    the text is whatever Whisper heard in a meeting and the label is whatever
    somebody typed into `referat label`, so an `<` in either is data. This is the
    same rule the VS Code sidebar followed by building every node with
    `textContent`, and the one thing worth carrying out of it.

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

    **The brackets come off, and the name is rendered bold instead.** This used
    to keep them, on the argument that `[[Anna]]` is how `/cleanup` spells a
    person and a link that quietly dropped them would look like a different
    notation. That argument was about the *file* and this is the *rendering*: a
    note is mostly people, and a page of `[[…]]` reads as markup somebody forgot
    to render. The notation is not lost — it is what `notes.md` still says, and
    what a copy out of this window puts back, which is :func:`unlink`'s job.

    Bold rather than bare, because a link Qt has already coloured and underlined
    still needs to read as *a person* beside a `[00:12:30]` that is not one.
    :func:`link_timestamps` keeps its brackets for the opposite reason: they are
    the timestamp's whole notation, and a bare `00:12:30` in prose is a duration.

    The two rewrites still cannot collide — a timestamp has no `[[` and this
    leaves `[00:12:30]` alone.

    Every wikilink is linked, including one naming a project or a topic. Nothing
    in a note distinguishes those from a person, and the people page answers a
    name nothing is filed under by saying so — see the module docstring.
    """

    def link(match: re.Match[str]) -> str:
        name = match.group(1).strip()
        if not name:
            return match.group(0)
        shown = MARKDOWN_ESCAPE_RE.sub(r"\\\1", name)
        return f"[**{shown}**](<{person_url(name)}>)"

    return WIKILINK_RE.sub(link, markdown)


def link_meetings(markdown: str, labels: dict[str, str]) -> str:
    """Rewrite each `[<meeting id>]` citation into a link that opens that meeting.

    `labels` is what each id should *read* as — the day summary shows the start
    time, since the page is already about one date — and it doubles as the guest
    list: **an id that is not in it is left as plain text**, which is what makes
    a citation of a meeting this machine does not have look like the mistake it
    is rather than like a link that does nothing.

    The destination goes in angle brackets for the reason
    :func:`link_timestamps`' does: it is not a shape CommonMark promises a bare
    destination survives, and the cost of being sure is two characters.
    """

    def link(match: re.Match[str]) -> str:
        meeting_id = match.group(1)
        if meeting_id not in labels:
            return match.group(0)
        return f"[{labels[meeting_id]}](<{meeting_url(meeting_id)}>)"

    return MEETING_ID_RE.sub(link, markdown)


LINKED_RE = re.compile(
    rf"\[((?:[^\\\[\]]|\\.|\](?!\())*)\]"
    rf"\(<?(?P<scheme>{PERSON_SCHEME}|{MEETING_SCHEME}|{SCHEME}):(?P<target>[^)>]*)>?\)"
)
"""A link this window put into the pane, as Qt writes it back out again.

Built from the three scheme constants rather than spelling them again, so a
scheme that ever changes changes here with it. **Longest first**, or `referat`
would be tried against `referat-person:` before the scheme that is actually
there — Python's alternation takes the first branch that matches, not the
longest.

**The link text holds brackets, and Qt escapes only some of them.** A timestamp
comes back as `[\\[00:12:30\\]](referat:00:12:30)` — measured, not assumed — so
the text group takes an escaped pair as one unit *and* allows a bare `]` that is
not the one closing the link, which is the one followed by `(`.

**An unescaped `[` is refused, and that is a bug fixed rather than a nicety.** It
was allowed, and the text group then ran straight through one, so a transcript
line reconstructing as `[00:00:09] [Niklas](referat-person:Niklas): …` matched
from the *timestamp's* bracket to the *name's* closing one and came out as
`00:00:09] [Niklas:` — a `[` deleted and the link left in. Nothing this module
writes puts a bare `[` in a link text: a timestamp's are escaped and a name is
plain or bold. So refusing one costs nothing and stops the overreach.
"""

UNESCAPE_RE = re.compile(r"\\([\[\]])")
"""Qt's escaping of the brackets our two rewrites deliberately kept.

Only the brackets, and only inside a link text this module put there. Undoing
every backslash escape in a fragment would be a different and wronger operation:
`\\*` in somebody's notes is a literal asterisk and must stay escaped, or the
Markdown that comes out means something the note did not say.
"""


def unlink(markdown: str, *, wiki: bool) -> str:
    """Take this window's own links back out of reconstructed Markdown.

    The inverse of :func:`link_timestamps`, :func:`link_people` and
    :func:`link_meetings` together, and it lives beside them so they cannot
    drift. A `referat:` link means nothing anywhere but here — pasted into Claude
    or a Google Doc it is a dead scheme wrapped around the text somebody actually
    wanted — so a copy collapses each one back to the notation the source file
    uses: `[00:12:30]`, `[[Anna]]`, `[2026-09-03_1408]`.

    **A person's link is rebuilt from its URL and not from its text**, which is
    what :func:`link_people` dropping the brackets costs: the rendering says
    `Anna` and the source said `[[Anna]]`, so the brackets have to come from
    somewhere, and the URL is the one place still holding the name exactly as it
    was written.

    `wiki` is what tells the two kinds of person link apart, and it is a
    parameter rather than a guess because the rendering cannot be read back for
    it. A `referat-person:` in the **notes** was a `[[Wikilink]]`; the identical
    link in the **transcript** was a speaker label, whose source notation is the
    bare `Anna:` it already renders as. Bracketing those would put wikilinks into
    a transcript that has none.

    A link to anything else is left alone. `notes.md` may carry a real `http://`
    URL, and that one survives a paste.
    """

    def plain(match: re.Match[str]) -> str:
        scheme, target = match.group("scheme"), match.group("target")
        if scheme == MEETING_SCHEME:
            return f"[{unquote(target)}]"
        if scheme == PERSON_SCHEME and wiki:
            return f"[[{unquote(target)}]]"
        return UNESCAPE_RE.sub(r"\1", match.group(1))

    return LINKED_RE.sub(plain, markdown)


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


class Pane(QTextBrowser):
    """One of the two panes, with copying that yields Markdown rather than words.

    **This exists because of where somebody actually reaches for a copy.** The
    two commands were on `Ctrl+Shift+C` and nowhere else, so the gesture people
    use — right-click, Copy, or `Ctrl+C` — kept going to Qt's own, which copies
    the *rendering*: a selection arrived without its `##`, its `-` or its `**`,
    with a theme-coloured HTML flavour beside it that Word and Google Docs
    prefer. Both of those are answered by routing the two gestures at the
    viewer's own commands instead.

    Its own context menu rather than an addition to `createStandardContextMenu`,
    which offers no handle on the Copy it builds beyond finding it by its
    translated label.
    """

    def __init__(self, viewer: Viewer) -> None:
        super().__init__(viewer)
        self._viewer = viewer
        # Both off, or QTextBrowser answers a click itself: an unknown scheme by
        # trying to load it into the pane, an http one by handing it to the
        # desktop with no say from here.
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)

    def contextMenuEvent(self, event: Any) -> None:
        """The two copy commands and a select-all, and nothing that writes."""
        menu = QMenu(self)
        menu.addAction(self._viewer.copy_markdown_action)
        menu.addAction(self._viewer.copy_formatted_action)
        menu.addSeparator()
        menu.addAction("Select all", self.selectAll)
        menu.exec(event.globalPos())

    def keyPressEvent(self, event: Any) -> None:
        """`Ctrl+C` copies Markdown, falling through to Qt when there is none.

        The fallthrough is not politeness: :meth:`Viewer.copy_markdown` answers
        an empty string when there is nothing to give — a placeholder pane, or a
        selection of whitespace — and swallowing the key there would make copy
        look broken rather than empty.
        """
        if event.matches(QKeySequence.StandardKey.Copy) and self._viewer.copy_markdown():
            return
        super().keyPressEvent(event)


class Viewer(QTabWidget):
    """The transcript and the notes for one meeting, and the links between them."""

    external_requested = Signal(QUrl)
    """A link that is neither a timestamp nor a name. The window decides what to do."""

    person_requested = Signal(str)
    """A speaker label or a `[[Wikilink]]` was clicked. The window opens that person.

    A name rather than a URL, because the receiver looks it up by name and this
    widget is the only thing that should know a name was ever percent-encoded.
    """

    copied = Signal(str)
    """What went onto the clipboard, for the window's status bar to say.

    Emitted rather than shown, because the status bar belongs to the window and
    this is a widget inside it. The sentence is written here, where the two
    flavours are told apart.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_copy_actions()
        self.transcript = self._browser()
        self.notes = self._browser()
        # Notes first, and that ordering is a claim about which document is the
        # point. The transcript is evidence and a source; the notes are what
        # somebody actually reads, and putting the transcript in front made every
        # meeting open on several hundred utterances.
        self.addTab(self.notes, "Notes")
        self.addTab(self.transcript, "Transcript")
        self.setCornerWidget(self._copy_button(), Qt.Corner.TopRightCorner)
        self._offsets: list[float] = []
        self._shown: str | None = None
        """The meeting id on screen, so a redraw of the same one keeps its scroll."""
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

    def _build_copy_actions(self) -> None:
        """The two commands, owned here and reached from the menu and the button.

        One `QAction` each, so the context menu and the corner button are two ways
        to invoke one command rather than two commands to keep saying the same
        thing.

        **Neither carries a real shortcut, and that is not an oversight.**
        `Ctrl+Shift+C` and `Ctrl+Alt+C` belong to the *window*, which dispatches
        them onto whichever page is in front — the Actions tab copies its own
        items with them. Registering the same keys again here would give one
        window two claims on one shortcut, which Qt answers by calling neither.
        So the keys are spelled into the label after a tab, which is how a Qt
        menu renders a shortcut hint, and the one place they are *bound* stays
        :meth:`referat.ui.window.CommandCenter._on_copy`.
        """
        self.copy_markdown_action = QAction("Copy as Markdown\tCtrl+Shift+C", self)
        self.copy_markdown_action.setToolTip("The source, as plain text - paste into Claude")
        self.copy_markdown_action.triggered.connect(lambda _checked=False: self.copy_markdown())
        self.copy_formatted_action = QAction("Copy as formatted text\tCtrl+Alt+C", self)
        self.copy_formatted_action.setToolTip(
            "Headings and bullets - paste into Google Docs or Word"
        )
        self.copy_formatted_action.triggered.connect(lambda _checked=False: self.copy_formatted())

    def _copy_button(self) -> QToolButton:
        """The corner of the tab bar: click copies Markdown, the arrow offers both.

        In the corner because that is where somebody looks for *copy this
        document*, and because the alternative — a fifth button beside `Tags…`,
        `Speakers…`, `Generate notes…` and `Delete…` — would put a reading
        command in a row of writing ones.

        The face is the icon alone. `setDefaultAction` would have carried the
        action's text into a tab bar corner, and *Copy as Markdown* is wider than
        the tabs it would sit beside.
        """
        button = QToolButton(self)
        button.setIcon(icons.glyph("copy", self.palette().windowText().color().name()))
        button.setToolTip("Copy this document (Ctrl+Shift+C)")
        button.setAutoRaise(True)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        button.clicked.connect(lambda _checked=False: self.copy_markdown())
        menu = QMenu(button)
        menu.addAction(self.copy_markdown_action)
        menu.addAction(self.copy_formatted_action)
        button.setMenu(menu)
        return button

    def copy_markdown(self) -> str:
        """Put the visible pane on the clipboard as Markdown source, plain.

        What pastes into Claude, and what `Ctrl+C` in a pane now does. Answers
        what was copied, or an empty string when there was nothing — which is
        what lets `Ctrl+C` fall through to Qt's own copy rather than swallowing
        the key on an empty pane.
        """
        return self._copy(formatted=False)

    def copy_formatted(self) -> str:
        """Put the visible pane on the clipboard as HTML over the same Markdown.

        What pastes into Google Docs with real headings and bullets. The HTML is
        :func:`referat.ui.richtext.markdown_html`'s and never this pane's: the
        pane is a rendering of *this window* — its theme, its font, its point
        size — and that is the paste this command exists to avoid.
        """
        return self._copy(formatted=True)

    def _copy(self, *, formatted: bool) -> str:
        """Both commands, since only the flavour differs. Says what went, for the bar."""
        markdown, what = self._markdown_for_copy()
        if not markdown:
            return ""
        richtext.to_clipboard(markdown, formatted=formatted)
        self.copied.emit(f"Copied {what} as {'formatted text' if formatted else 'Markdown'}.")
        return what

    def _markdown_for_copy(self) -> tuple[str, str]:
        """The Markdown behind the visible pane, and what to call it.

        **The whole document is the file.** Both panes keep their source —
        `notes.md` and `transcript.md` as they are on disk — so a copy of
        everything is the file rather than the rendering, and no `referat:` link
        or rewritten timestamp is in it to begin with.

        **A selection is sliced out of that same source where it can be**, by
        :meth:`_source_selection`, and reconstructed from the rendering only
        where it cannot. The order matters and is not a preference: Qt's Markdown
        writer **drops `**bold**` and `*italic*` entirely** — measured, on a
        round trip through `setMarkdown` — and the notes put every action item's
        owner in bold, so a reconstruction is a lossy last resort rather than the
        plan.

        A selection still wins over the whole document, because somebody who
        selected three lines meant three lines.
        """
        browser = self.currentWidget()
        if not isinstance(browser, QTextBrowser):
            return "", ""
        source = self._raw.get(browser)
        cursor = browser.textCursor()
        if cursor.hasSelection():
            sliced = self._source_selection(browser, source) if source else None
            if sliced is None:
                sliced = unlink(cursor.selection().toMarkdown(), wiki=browser is self.notes)
            return sliced.strip(), "the selection"
        whole = self.tabText(self.currentIndex()).lower()
        return (source or browser.toPlainText()).strip(), f"the {whole}"

    def _source_selection(self, browser: QTextBrowser, source: str) -> str | None:
        """The selected blocks, taken out of the *source*, or None if they cannot be.

        `setMarkdown` keeps no map from the document it built back to the text it
        was given, and this is the map: :func:`referat.ui.richtext.blocks` splits
        the source exactly where Qt splits it into `QTextBlock`s — a heading, a
        paragraph however many lines it wrapped over, and one per list item.

        **It refuses rather than guesses.** If the two counts disagree the source
        is not the document this pane is showing — an old transcript written
        before the entries were separated by blank lines is one real way that
        happens — and a mapping built on a mismatch would hand somebody the wrong
        three paragraphs, which is worse than the reconstruction it falls back to.

        A selection inside a single block is also refused, deliberately: half a
        sentence is what was selected, and returning the whole paragraph around it
        would copy words nobody asked for. The reconstruction handles that case
        well, since there is no block structure in it to lose.
        """
        parts = richtext.blocks(source)
        document = browser.document()
        numbers = [
            block.blockNumber()
            for block in _iterate_blocks(document)
            if block.text().strip()
        ]
        if len(numbers) != len(parts):
            log.debug(
                "no source map: %d source blocks against %d rendered", len(parts), len(numbers)
            )
            return None
        cursor = browser.textCursor()
        first = bisect_left(numbers, document.findBlock(cursor.selectionStart()).blockNumber())
        last = bisect_right(numbers, document.findBlock(cursor.selectionEnd()).blockNumber()) - 1
        if first > last or first >= len(parts) or last < 0:
            return None
        if first == last:
            return None
        return richtext.join_blocks(parts[first : last + 1])

    def _browser(self) -> Pane:
        browser = Pane(self)
        browser.anchorClicked.connect(self._on_anchor)
        return browser

    # --- Filling it ---------------------------------------------------------

    def clear_meeting(self, message: str) -> None:
        """Both panes say the same thing: there is nothing selected, or nothing there."""
        self._shown = None
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
        self._shown = None
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
        # The same meeting drawn again -- a refresh after tagging, F5, a
        # transition -- keeps both panes where they were. It used to jump to
        # the top on every redraw, which after tagging a meeting looked like
        # the window losing its place. A different meeting starts at the top.
        same = self._shown == document.get("id")
        positions = (
            {pane: pane.verticalScrollBar().value() for pane in (self.transcript, self.notes)}
            if same
            else {}
        )
        self._shown = document.get("id")
        self.transcript.setHtml(render_transcript_html(document))
        self._offsets = [float(entry["at"]) for entry in document["entries"]]
        # Kept for `_markdown_for_copy`, which hands over the source rather than
        # the rendering. **Both** panes now, which is a correction: the
        # transcript's plain text looked close enough to its source to skip, and
        # is not it -- the `## Meeting ...` header is drawn as bold text, and the
        # blank line between entries that keeps a Markdown reader from running
        # them into one paragraph is a paragraph break the plain text spells as
        # one newline. `transcript.md` itself has both.
        self._raw = {}
        if document.get("markdown"):
            self._raw[self.transcript] = document["markdown"]
        if notes is not None:
            self._raw[self.notes] = notes
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
        if positions:
            for pane, value in positions.items():
                pane.verticalScrollBar().setValue(value)
        else:
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
