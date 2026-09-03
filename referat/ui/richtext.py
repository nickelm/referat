"""Markdown into the HTML a paste carries, for Google Docs and Word.

**This is the manual half of step 13.** A note reaches a Google Doc in two ways:
`referat project sync` will translate it into `batchUpdate` requests, and a
person selects it in the command center and pastes it. Those two must render the
same note the same way, so this module answers exactly the questions
`referat/digest.py` is specified to answer — a `[[Wikilink]]` becomes bold with
the brackets stripped, because it has no target outside this machine, and no raw
Markdown character is left in the output.

**The subset is the one `/cleanup` is required to write**, and it is written down
in `templates/meetings/.claude/commands/cleanup.md` rather than here: `#`, `##`
and `###`, `**bold**`, `*italic*`, `` `code` ``, one level of `-` bullets,
`[text](url)`, `[[Wikilinks]]` and ordinary paragraphs. Anything outside it is
emitted as the literal characters it is made of, which is the same promise that
file makes about the doc converter — a `####` heading arrives as a paragraph
reading `#### ...` rather than being silently swallowed.

**Nothing here carries a colour, a font or a size.** That is the whole point.
Qt's own HTML clipboard flavour is a rendering of *this window* — the theme's
background, the pane's font, a size in points — and pasting it into Word or Docs
is what made a note arrive as dark-background monospace. Plain structural HTML
picks up the styling of the document it lands in, which is what somebody
pasting into a Google Doc is asking for.

**No import from :mod:`referat.transcribe`, deliberately**, which is why a
transcript pastes as one plain paragraph per entry with its `[HH:MM:SS] Name:`
prefix as text rather than with a bold label. The shape of an entry is that
module's to describe, and a second parser for it in the UI is precisely what
:func:`referat.cli.transcript_document` exists to prevent. A bold speaker label
is not worth a copy of the format.

:func:`markdown_html` is pure text in and text out, so it is checkable against a
real `notes.md` from a prompt with no window and no `QApplication` anywhere.
:func:`to_clipboard` is the Qt half, and it is here rather than in the two pages
that call it because *what a copy out of this UI puts on the clipboard* is one
decision: plain text for Markdown, and HTML with the Markdown beside it for
formatted. Two surfaces implementing that separately is how one of them ends up
offering Word an HTML flavour nobody asked for.
"""

from __future__ import annotations

import html
import re

from PySide6.QtCore import QMimeData
from PySide6.QtGui import QGuiApplication

HEADING_RE = re.compile(r"^(#{1,3}) +(.+)$")
"""An ATX heading, one to three deep.

`####` deliberately does not match — the hashes run past the three the subset
allows, the ` ` this pattern requires is not there, and the line falls through to
a paragraph carrying its own hashes. That is the *raw characters* rule doing
what it says rather than a heading level quietly being rounded down.
"""

BULLET_RE = re.compile(r"^[-*] +(.+)$")
"""One list item, at the left margin and nowhere else.

Anchored with no leading whitespace on purpose. The notes wrap a long bullet onto
following **indented** lines — the meetings folder's `CLAUDE.md` says so, and the
action-item parser depends on it — so an indented `- ` is a continuation of the
item above rather than a nested list, and nesting is outside the subset anyway.
"""

TASK_RE = re.compile(r"^\[([ xX])\] +(.*)$")
"""A task-list item, `- [ ]` or `- [x]`, as :func:`referat.cli.actions_markdown`
writes one.

Outside the subset `/cleanup` may write and inside the one this renders,
deliberately: the action items page copies through here too, a task list is the
only thing that page produces, and `[ ]` left as two literal characters in a
Google Doc is the sort of small wrongness nobody reports and everybody notices.
The box becomes the character a document would have used.
"""

BOXES = {" ": "☐", "x": "☑", "X": "☑"}
"""Ballot box, and ballot box with check. Plain characters rather than a real
list style, because a Docs paste has no checkbox to become.

**Not a contradiction of :func:`referat.cli.actions_markdown`'s plain-ASCII
rule**, which exists because that string goes onto a clipboard and down a pipe
into a console whose code page renders an em dash as a replacement character.
These two characters are in the *HTML* flavour only — the one that declares
UTF-8 and is going into a document — and the Markdown flavour is still the
`- [ ]` that function wrote.
"""

LINK_SCHEME_RE = re.compile(r"^(?:https?|mailto):", re.IGNORECASE)
"""What makes a link destination worth keeping as a link.

`notes.md` carries `[transcript.md](transcript.md)` on its second line, and a
relative path is a link to nothing once the note is inside a Google Doc — it
would resolve against the doc's own host and dead-end there. So a destination
with no scheme is rendered as its text alone. A visible dead link is worse than
plain words, and this is the same judgement `link_timestamps` makes in the
viewer for `referat:` targets.
"""

INLINE_RE = re.compile(
    r"(?P<escape>\\(?P<escaped>[\\`*_{}\[\]()#+\-.!]))"
    r"|(?P<code>`(?P<code_text>[^`]+)`)"
    r"|(?P<bold>\*\*(?P<bold_text>.+?)\*\*)"
    r"|(?P<wiki>\[\[(?P<wiki_text>[^\[\]]+)\]\])"
    r"|(?P<link>\[(?P<link_text>[^\[\]]*)\]\(<?(?P<link_url>[^)>]*)>?\))"
    r"|(?P<italic>\*(?P<italic_text>[^*]+)\*)"
)
"""Every inline form in one alternation, scanned once left to right.

Order is load-bearing and each place in it is an argument:

* **escape** first, so a `\\*` Qt wrote when reconstructing a selection is one
  literal asterisk and never the start of emphasis.
* **code** before everything else that can appear inside it, because the inside
  of a code span is characters and not markup.
* **bold** before **italic**, or `**text**` is read as an empty italic followed
  by another one.
* **wiki** before **link**, since `[[Name]]` shares its opening bracket with a
  link and the two have to be told apart by the second one.

Scanning once rather than substituting six patterns in turn is what keeps a
match's *contents* from being re-scanned by accident; the two that genuinely
nest — bold and italic — recurse explicitly, which is how `**[[Anna]]** — ...`
comes out as a bold name rather than a bold `[[Anna]]`.
"""

DOCUMENT = '<html><head><meta charset="utf-8"></head><body>{body}</body></html>'
"""The wrapper. A charset, because a note is full of `·` and em dashes.

Qt hands `text/html` to Windows as CF_HTML, which is defined as UTF-8, and the
meta tag costs nothing and settles it for whatever reads the flavour directly.
"""


def to_clipboard(markdown: str, *, formatted: bool) -> None:
    """One Markdown document onto the clipboard, in one flavour or in two.

    **Plain text alone is the default and the safe one.** Qt offers an HTML
    flavour beside the text, and Word, Google Docs and Outlook all prefer it when
    it is there — which is how a paste out of this window arrived as
    theme-coloured monospace, and exactly what the meetings folder's
    `editor.copyWithSyntaxHighlighting: false` prevents for VS Code. `setText`
    writes one flavour and leaves them nothing to prefer.

    `formatted` is the same choice made deliberately: somebody asked for
    formatted text by name, so Docs taking the HTML is the intent rather than an
    accident. The Markdown still goes on beside it, because a plain-text target
    should get Markdown rather than nothing.
    """
    if not formatted:
        QGuiApplication.clipboard().setText(markdown)
        return
    data = QMimeData()
    data.setText(markdown)
    data.setHtml(markdown_html(markdown))
    QGuiApplication.clipboard().setMimeData(data)


def markdown_html(markdown: str) -> str:
    """One Markdown document as the HTML flavour of a clipboard.

    Structure only: headings, paragraphs, one level of bullets, bold, italic,
    code and links. No `style`, no `font`, no colour — see the module docstring
    for why that is the requirement rather than an omission.
    """
    return DOCUMENT.format(body=_body(markdown))


HEADING, PARAGRAPH, ITEM = "heading", "paragraph", "item"
"""The three kinds of block this subset has. Not an enum: they are three strings
compared in two functions, and the flat-module rule in `CLAUDE.md` is a rule
about not building machinery nobody asked for."""


def blocks(markdown: str) -> list[tuple[str, str]]:
    """A document as its renderable blocks: one heading, paragraph or list item each.

    Each is `(kind, source)`, and the source is the Markdown for that block alone
    — a heading with its hashes, an item with its `- `, a paragraph with its
    wrapped lines joined by a space. So the list can be rendered
    (:func:`markdown_html`) *or* sliced and joined back into Markdown
    (:func:`join_blocks`), which is what a copy of a selection out of the command
    center does.

    **One block here is one `QTextBlock` after `setMarkdown`, and that
    correspondence is the point.** Qt gives a heading one block, a paragraph one
    block however many source lines it wrapped over, and each list item its own —
    exactly the split this makes. That is what lets a selection be mapped back
    onto the *source* rather than reconstructed from the rendering, which matters
    because Qt's Markdown writer drops `**bold**` and `*italic*` on the way out.
    The caller checks the two counts agree before trusting it, so a document this
    misreads costs the mapping and not the copy.

    A blank line closes whatever is open, which is the one structural rule
    Markdown has. The continuation cases are what make this more than a `for`
    loop: the notes wrap their prose *and* their bullets, and joining those back
    is what keeps a wrapped action item one bullet rather than a bullet and a
    stray paragraph.
    """
    out: list[tuple[str, str]] = []
    paragraph: list[str] = []

    def close_paragraph() -> None:
        if paragraph:
            out.append((PARAGRAPH, " ".join(paragraph)))
            paragraph.clear()

    open_list = False
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped:
            close_paragraph()
            open_list = False
            continue
        heading = HEADING_RE.match(line)
        if heading is not None:
            close_paragraph()
            open_list = False
            out.append((HEADING, f"{heading.group(1)} {heading.group(2).strip()}"))
            continue
        bullet = BULLET_RE.match(line)
        if bullet is not None:
            close_paragraph()
            open_list = True
            out.append((ITEM, f"- {bullet.group(1).strip()}"))
            continue
        if open_list:
            # An indented or otherwise plain line under an open list is the rest
            # of that item, not a new paragraph inside the list.
            kind, text = out[-1]
            out[-1] = (kind, f"{text} {stripped}")
            continue
        paragraph.append(stripped)
    close_paragraph()
    return out


def join_blocks(parts: list[tuple[str, str]]) -> str:
    """Blocks back into one Markdown document. The inverse of :func:`blocks`.

    Two consecutive items are joined by a single newline and everything else by a
    blank line, which is what keeps a slice of five bullets one list rather than
    five paragraphs that happen to start with a dash.
    """
    out = ""
    previous = ""
    for kind, text in parts:
        if out:
            out += "\n" if kind == ITEM and previous == ITEM else "\n\n"
        out += text
        previous = kind
    return out


def _body(markdown: str) -> str:
    """The blocks as HTML, with a run of items wrapped in one list."""
    out: list[str] = []
    items: list[str] = []

    def close_list() -> None:
        if items:
            out.append("<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>")
            items.clear()

    for kind, text in blocks(markdown):
        # Both prefixes are taken back off by position rather than by a second
        # match: `blocks` wrote them, one line above, in exactly this shape.
        if kind == ITEM:
            item = text[2:]
            task = TASK_RE.match(item)
            if task is not None:
                items.append(f"{BOXES[task.group(1)]} {_inline(task.group(2))}")
            else:
                items.append(_inline(item))
            continue
        close_list()
        if kind == HEADING:
            hashes, _, title = text.partition(" ")
            out.append(f"<h{len(hashes)}>{_inline(title)}</h{len(hashes)}>")
        else:
            out.append(f"<p>{_inline(text)}</p>")
    close_list()
    return "".join(out)


def _inline(text: str) -> str:
    """One block's text with its inline markup applied and everything else escaped.

    The gaps between matches are the only place raw text reaches the output, and
    they all go through :func:`html.escape`. That is not a formality: this text
    is somebody's notes about a meeting, and a `<` in it is a character rather
    than a tag — the same rule :func:`referat.ui.viewer.render_transcript_html`
    follows for what Whisper heard.
    """
    out: list[str] = []
    position = 0
    for match in INLINE_RE.finditer(text):
        out.append(html.escape(text[position : match.start()]))
        out.append(_span(match))
        position = match.end()
    out.append(html.escape(text[position:]))
    return "".join(out)


def _span(match: re.Match[str]) -> str:
    """One inline match as HTML. Bold and italic recurse; nothing else does."""
    if match.group("escape"):
        return html.escape(match.group("escaped"))
    if match.group("code"):
        return f"<code>{html.escape(match.group('code_text'))}</code>"
    if match.group("bold"):
        return f"<b>{_inline(match.group('bold_text'))}</b>"
    if match.group("wiki"):
        # Bold and unbracketed, which is what `referat/digest.py` is specified to
        # do with one: a wikilink names a person or a project and has no target
        # anywhere outside the meetings folder.
        return f"<b>{html.escape(match.group('wiki_text').strip())}</b>"
    if match.group("link"):
        inner = _inline(match.group("link_text"))
        url = match.group("link_url").strip()
        if not LINK_SCHEME_RE.match(url):
            return inner
        return f'<a href="{html.escape(url, quote=True)}">{inner}</a>'
    return f"<i>{_inline(match.group('italic_text'))}</i>"
