"""The constrained Markdown subset `/cleanup` writes, described exactly once.

**Two things render a `notes.md` and they must not disagree.** A person selects
part of a note in the command center and pastes it into a Google Doc, which is
`referat/ui/richtext.py`; and `referat project sync` translates the whole note
into `batchUpdate` requests and pushes it into the same kind of doc, which is
`referat/digest.py`. If those two ever answered a question differently — whether
a `[[Wikilink]]` keeps its brackets, whether a relative link stays a link — the
same note would arrive in the same document looking like two different notes,
depending on how it got there. That is the drift this codebase keeps deleting,
and this module is where it is prevented: the *grammar* lives here, and each
renderer is a walk over it.

**Why it is a flat module and not part of `referat/ui/`.** `richtext.py` imports
PySide6 at module scope and lives in the one sub-package, which
`referat/ui/__init__.py` documents as the command center. `digest.py` is a
top-level module that opens a network connection and must not import a GUI
toolkit to do it, so it cannot reach into `referat/ui/` — that would invert the
layering and put Qt behind a network push. The Conventions rule permits this
exactly as written: one package, flat modules, one sub-package, and nothing else
grows one.

**The subset itself is written down in
`templates/meetings/.claude/commands/cleanup.md`, not here.** That file is the
authority on what the prompt may emit; this one is the authority on how to read
it. What it covers: `#`, `##` and `###` headings, `**bold**`, `*italic*`,
`` `code` ``, one level of `-` bullets, `[text](url)`, `[[Wikilinks]]` and
ordinary paragraphs. Anything outside it is emitted as the literal characters it
is made of — a `####` heading arrives as a paragraph reading `#### ...` rather
than being silently swallowed, which is the same promise that prompt makes.

**Nothing here renders.** No HTML, no Docs request, no colour, no font. This
module imports `re` and nothing else, so it is drivable from a prompt with no
window, no `QApplication` and no `digest` extra anywhere.

What deliberately stayed in `richtext.py`: the recursive walk that turns a match
into HTML. The two renderers each keep their own, because they nest differently
— `richtext` recurses to produce `<b>a <i>b</i> c</b>` while `digest` records
overlapping spans against one flat string — and flattening them into a shared
token list would have changed `richtext`'s output. What keeps the two walks from
drifting is not a shared function but `scripts/digest_report.py`, which renders
every real `notes.md` through both and compares the plain text they produce.
"""

from __future__ import annotations

import re

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

That the subset has exactly one bullet level is load-bearing twice over. Here it
makes a continuation unambiguous; in :mod:`referat.digest` it is what lets
`createParagraphBullets` be the last request in a batch, since the Docs API
strips leading tabs when it applies bullets and stripping a tab would move every
index the batch had already computed.
"""

TASK_RE = re.compile(r"^\[([ xX])\] +(.*)$")
"""A task-list item, `- [ ]` or `- [x]`, as :func:`referat.cli.actions_markdown`
writes one.

Outside the subset `/cleanup` may write and inside the one this describes,
deliberately: the action items page copies through here too, a task list is the
only thing that page produces, and `[ ]` left as two literal characters in a
Google Doc is the sort of small wrongness nobody reports and everybody notices.
The box becomes the character a document would have used.
"""

BOXES = {" ": "☐", "x": "☑", "X": "☑"}
"""Ballot box, and ballot box with check. Plain characters rather than a real
list style, because neither a Docs paste nor a Docs `insertText` has a checkbox
to become.

**Not a contradiction of :func:`referat.cli.actions_markdown`'s plain-ASCII
rule**, which exists because that string goes onto a clipboard and down a pipe
into a console whose code page renders an em dash as a replacement character.
These two characters are for a *document* — the HTML clipboard flavour, which
declares UTF-8, and the text of a Google Doc — and the Markdown flavour is still
the `- [ ]` that function wrote.
"""

LINK_SCHEME_RE = re.compile(r"^(?:https?|mailto):", re.IGNORECASE)
"""What makes a link destination worth keeping as a link.

`notes.md` carries `[transcript.md](transcript.md)` on its second line, and a
relative path is a link to nothing once the note is inside a Google Doc — it
would resolve against the doc's own host and dead-end there. So a destination
with no scheme is rendered as its text alone. A visible dead link is worse than
plain words, and this is the same judgement `link_timestamps` makes in the
viewer for `referat:` targets.

It matters more for the digest than for a paste: a pasted note is read by the
person who pasted it, and a pushed one is read by whoever the doc is shared
with, who has no `transcript.md` and never will — the transcript does not leave
this machine.
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
nest — bold and italic — are recursed into explicitly by each renderer, which is
how `**[[Anna]]** — ...` comes out as a bold name rather than a bold `[[Anna]]`.
"""

HEADING, PARAGRAPH, ITEM = "heading", "paragraph", "item"
"""The three kinds of block this subset has. Not an enum: they are three strings
compared in a handful of functions, and the flat-module rule in `CLAUDE.md` is a
rule about not building machinery nobody asked for."""


def blocks(markdown: str) -> list[tuple[str, str]]:
    """A document as its renderable blocks: one heading, paragraph or list item each.

    Each is `(kind, source)`, and the source is the Markdown for that block alone
    — a heading with its hashes, an item with its `- `, a paragraph with its
    wrapped lines joined by a space. So the list can be rendered
    (:func:`referat.ui.richtext.markdown_html`, :func:`referat.digest.render_block`)
    *or* sliced and joined back into Markdown (:func:`join_blocks`), which is
    what a copy of a selection out of the command center does.

    **One block here is one `QTextBlock` after `setMarkdown`, and that
    correspondence is the point.** Qt gives a heading one block, a paragraph one
    block however many source lines it wrapped over, and each list item its own —
    exactly the split this makes. That is what lets a selection be mapped back
    onto the *source* rather than reconstructed from the rendering, which matters
    because Qt's Markdown writer drops `**bold**` and `*italic*` on the way out.
    The caller checks the two counts agree before trusting it, so a document this
    misreads costs the mapping and not the copy.

    That the split is described against Qt does **not** make it a UI concern.
    It is the split Markdown itself makes; Qt is where it happened to be
    measured, and the digest wants the same one for a different reason — a block
    here is one Docs paragraph there.

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
