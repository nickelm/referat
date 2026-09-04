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

# Aliased, because three functions in this file take a parameter called
# `markdown` and a module of that name would be shadowed inside every one
# of them.
from referat import markdown as md

# The grammar moved to `referat/markdown.py` at build step 13, so that
# `referat/digest.py` could read the same subset without importing a GUI
# toolkit to do it. Re-exported under their old names because
# `referat/ui/viewer.py` maps a selection back onto the source through
# `richtext.blocks` and `richtext.join_blocks`, and *what a copy out of this
# UI does* is one decision that stays described in one file.
HEADING_RE = md.HEADING_RE
BULLET_RE = md.BULLET_RE
TASK_RE = md.TASK_RE
BOXES = md.BOXES
LINK_SCHEME_RE = md.LINK_SCHEME_RE
INLINE_RE = md.INLINE_RE
HEADING, PARAGRAPH, ITEM = md.HEADING, md.PARAGRAPH, md.ITEM
blocks = md.blocks
join_blocks = md.join_blocks

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
