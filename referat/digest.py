"""A meeting's `notes.md` as a block in a Google Doc: the translator and the diff.

**Pure, and that is the whole design.** Nothing here imports `googleapiclient`,
`referat.gdocs` or anything that opens a socket, so every part of build step 13
that carries index arithmetic can be driven from a prompt with no network, no
credentials and no `digest` extra installed. `scripts/digest_report.py` is what
drives it, against the real `notes.md` files in the meetings folder.

That split is not tidiness. The dangerous half of this step is arithmetic --
UTF-16 offsets, style ranges that must land on the right substring, and block
operations that have to be applied in an order that keeps stale indices valid --
and arithmetic you can only exercise by writing into somebody's document is
arithmetic nobody exercises. So the requests are *built* here as plain dicts and
*posted* in :mod:`referat.gdocs`, and the seam between them is a list of
dictionaries you can print.

**What a block is.** One meeting, rendered into a Google Doc tab as:

    [referat:2026-09-03_1408]        <- the anchor, small and gray
    2026-09-03 - Planning the ...    <- Heading 3, the date and the title
    ...                              <- the note, its ## and ### as Heading 4/5

A block runs from its anchor to the next one. The anchor is a **visible text
paragraph** rather than a Docs named range, which is the API's own mechanism and
the fragile one: named ranges are invisible to a person editing the doc,
destroyed along with their content, and not carried by a copy of the document.
Deleting a text anchor merely makes the next sync re-append that block, which is
the failure this codebase prefers -- recoverable and obvious.

**Only `notes.md` is ever read here.** Never `transcript.md`, never a WAV, never
`.voices/`. That is the Convention this module is most able to break, because it
is the thing that sends prose somewhere else, so it is restated in the one file
that does the sending and in the one that does the rendering.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from referat import index, markdown, paths

if TYPE_CHECKING:  # pragma: no cover - typing only
    from referat.meeting import Meeting
    from referat.projects import ProjectsDB

log = logging.getLogger(__name__)


class DigestError(Exception):
    """The translator produced something that must not be sent.

    Raised only by :func:`check_block`'s callers, and it means a bug in this
    module rather than a bad note: the acceptance check found raw Markdown in
    text that is about to become a document's prose. A sync skips that one
    meeting, names it, and carries on with the rest -- one unrenderable note may
    not cost a whole project's digest.
    """


# --- Offsets ----------------------------------------------------------------


def u16(text: str) -> int:
    """A string's length in the UTF-16 code units the Docs API indexes by.

    **Never `len()`.** Docs counts positions in UTF-16 code units, so anything
    outside the Basic Multilingual Plane -- an emoji, most of them -- is two
    where Python says one, and every style range after it lands one place early.
    The notes are full of `-`, `.` and `'` characters that happen to agree, which
    is exactly what makes this the kind of bug that works until it does not.

    Accumulated incrementally rather than recomputed from a slice, because the
    text is built as it is measured and a second pass over it would be a second
    chance to disagree with the first.
    """
    return len(text.encode("utf-16-le")) // 2


# --- The anchor -------------------------------------------------------------

ANCHOR_TEMPLATE = "[referat:{}]"
ANCHOR_RE = re.compile(r"^\[referat:(\d{4}-\d{2}-\d{2}_\d{4}(?:_\d+)?)\]$")
"""What a block is found by, written and read.

The id pattern carries the `_2` / `_3` collision suffix `paths.new_meeting_dir`
mints, because a meeting that collided is a meeting like any other and an anchor
this pattern refused to read would be a block the reconciler re-appended forever.
"""

ANCHOR_GRAY = {"color": {"rgbColor": {"red": 0.55, "green": 0.55, "blue": 0.55}}}
ANCHOR_POINTS = 8

# --- Paragraph kinds --------------------------------------------------------

ANCHOR, H3, H4, H5, PARAGRAPH, ITEM = "anchor", "h3", "h4", "h5", "paragraph", "item"

NAMED_STYLES = {
    ANCHOR: "NORMAL_TEXT",
    H3: "HEADING_3",
    H4: "HEADING_4",
    H5: "HEADING_5",
    PARAGRAPH: "NORMAL_TEXT",
    ITEM: "NORMAL_TEXT",
}
"""Each paragraph kind's Docs named style.

The date line is Heading 3, so the note's own `##` lands as Heading 4 and `###`
as Heading 5 beneath it -- the block keeps its internal shape while sitting one
level down inside a document that has its own headings above it.
"""


@dataclass(frozen=True)
class Span:
    """One run of character styling, in UTF-16 units relative to the block's text."""

    start: int
    end: int
    kind: str  # bold | italic | code | link | anchor
    url: str = ""


@dataclass(frozen=True)
class Para:
    """One paragraph's extent, **including its trailing newline**.

    The newline is inside the range because that is what a Docs paragraph is: a
    `updateParagraphStyle` over a range that stops short of it styles nothing.
    """

    start: int
    end: int
    kind: str


@dataclass(frozen=True)
class Block:
    """One meeting, ready to be inserted: the text, and where the styling goes."""

    meeting_id: str
    text: str
    spans: list[Span] = field(default_factory=list)
    paras: list[Para] = field(default_factory=list)

    @property
    def width(self) -> int:
        return u16(self.text)


# --- Rendering --------------------------------------------------------------


def _inline(text: str) -> tuple[str, list[Span]]:
    """One paragraph's source as plain text plus the styling that goes over it.

    A walk over :data:`referat.markdown.INLINE_RE`, recursing where the subset
    genuinely nests. It answers exactly the questions
    :func:`referat.ui.richtext.markdown_html` answers, and the two are checked
    against each other over every real `notes.md` by
    `scripts/digest_report.py` -- which is what keeps a note pushed into a doc
    and a note pasted into one from reading as two different notes.

    The one shape that differs is structural rather than a decision: HTML nests,
    so `richtext` produces `<b>a <i>b</i> c</b>`; Docs styles ranges over one
    flat string, so this produces two overlapping spans. Same rendering, and
    neither could have been expressed the other's way.
    """
    out: list[str] = []
    spans: list[Span] = []
    width = 0

    def emit(chunk: str) -> int:
        nonlocal width
        start = width
        if chunk:
            out.append(chunk)
            width += u16(chunk)
        return start

    def nest(inner: str, kind: str | None, url: str = "") -> None:
        """Render `inner` recursively and, unless `kind` is None, style the whole of it."""
        nonlocal width
        sub, sub_spans = _inline(inner)
        start = width
        out.append(sub)
        width += u16(sub)
        spans.extend(
            Span(s.start + start, s.end + start, s.kind, s.url) for s in sub_spans
        )
        if kind is not None:
            spans.append(Span(start, width, kind, url))

    position = 0
    for match in markdown.INLINE_RE.finditer(text):
        emit(text[position : match.start()])
        if match.group("escape"):
            emit(match.group("escaped"))
        elif match.group("code"):
            start = emit(match.group("code_text"))
            spans.append(Span(start, width, "code"))
        elif match.group("bold"):
            nest(match.group("bold_text"), "bold")
        elif match.group("wiki"):
            # Bold, with the brackets taken off. A wikilink names a person or a
            # project and has no target anywhere outside this machine, so the
            # brackets would be markup nobody rendered -- and the same decision
            # `richtext._span` makes, which is why they agree in a shared doc.
            start = emit(match.group("wiki_text").strip())
            spans.append(Span(start, width, "bold"))
        elif match.group("link"):
            url = match.group("link_url").strip()
            keep = markdown.LINK_SCHEME_RE.match(url) is not None
            nest(match.group("link_text"), "link" if keep else None, url if keep else "")
        elif match.group("italic"):
            nest(match.group("italic_text"), "italic")
        position = match.end()
    emit(text[position:])
    return "".join(out), spans


def render_block(meeting_id: str, date: str, title: str, notes_md: str) -> Block:
    """One meeting's notes as a block. Strings in, `Block` out -- no `Meeting`, no config.

    Takes the pieces rather than the record so a script can render a block from a
    file and a made-up id, which is what makes the whole translator checkable
    without a meetings folder.

    **The note's own `#` H1 is dropped**, and only ever the first one. It is the
    same string the Heading 3 date line above it already carries -- that is where
    :func:`referat.index.meeting_title` gets it -- so keeping it would make every
    block in the document announce its title twice. A `#` further down is left
    alone, because :func:`referat.index.meeting_title` does not treat that as a
    title either and this must not disagree with it.
    """
    parts = markdown.blocks(notes_md)
    if parts and parts[0][0] == markdown.HEADING and parts[0][1].startswith("# "):
        parts = parts[1:]

    chunks: list[str] = []
    spans: list[Span] = []
    paras: list[Para] = []
    width = 0

    def add(line: str, kind: str, line_spans: list[Span] = ()) -> int:
        nonlocal width
        start = width
        chunks.append(line + "\n")
        width += u16(line) + 1
        spans.extend(
            Span(s.start + start, s.end + start, s.kind, s.url) for s in line_spans
        )
        paras.append(Para(start, width, kind))
        return start

    anchor = ANCHOR_TEMPLATE.format(meeting_id)
    start = add(anchor, ANCHOR)
    # Over the anchor's text and not its newline: styling the newline would carry
    # the gray into whatever paragraph a later edit merges it with.
    spans.append(Span(start, start + u16(anchor), "anchor"))

    add(f"{date} — {title}", H3)

    for kind, source in parts:
        if kind == markdown.HEADING:
            hashes, _, heading = source.partition(" ")
            line, line_spans = _inline(heading)
            add(line, H5 if len(hashes) >= 3 else H4, line_spans)
        elif kind == markdown.ITEM:
            # The `- ` comes off by position rather than by a second match:
            # `markdown.blocks` wrote it, in exactly this shape. It must not
            # survive into the text, because `createParagraphBullets` converts
            # existing paragraphs and would leave a dash beside its own bullet.
            item = source[2:]
            task = markdown.TASK_RE.match(item)
            if task is not None:
                line, line_spans = _inline(task.group(2))
                box = f"{markdown.BOXES[task.group(1)]} "
                shift = u16(box)
                line = box + line
                line_spans = [
                    Span(s.start + shift, s.end + shift, s.kind, s.url)
                    for s in line_spans
                ]
            else:
                line, line_spans = _inline(item)
            add(line, ITEM, line_spans)
        else:
            line, line_spans = _inline(source)
            add(line, PARAGRAPH, line_spans)

    return Block(meeting_id, "".join(chunks), spans, paras)


def block_for(meeting: Meeting) -> Block:
    """The block for one meeting, read off the disk. Raises `DigestError` if it fails the check."""
    notes = (meeting.dir / paths.NOTES_MD).read_text(encoding="utf-8")
    date = meeting.started_at.strftime("%Y-%m-%d")
    block = render_block(meeting.id, date, index.meeting_title(meeting), notes)
    if (complaint := check_block(block)):
        raise DigestError(f"{meeting.id}: {complaint}")
    return block


# --- The acceptance check ---------------------------------------------------

_NEVER = ("**", "](", "[[")


def check_block(block: Block) -> str:
    """Raw Markdown that must never reach a document's prose. A complaint, or `""`.

    **Applied per paragraph against the kind it was given, not as a flat scan**,
    and that distinction is the whole correctness of this function. A flat "no
    leading `#`" would fire on a deliberate pass-through: `markdown.HEADING_RE`
    matches one to three hashes, so a `####` line is documented to fall through
    to a *paragraph carrying its own hashes*, which is the raw-characters rule
    doing what it says rather than a heading level being quietly rounded down.
    Refusing it here would turn a documented behaviour into an error.

    So a heading paragraph starting with `#` is a bug (its hashes should have
    been consumed), an item starting with `- ` is a bug (the bullet marker would
    sit beside the bullet Docs draws), and a plain paragraph starting with `#` is
    not a bug at all.
    """
    for para in block.paras:
        line = block.text[_slice(block.text, para.start, para.end)].rstrip("\n")
        if para.kind in (H3, H4, H5) and line.startswith("#"):
            return f"a {para.kind} paragraph still carries its hashes: {line!r}"
        if para.kind == ITEM and (line.startswith("- ") or line.startswith("* ")):
            return f"a list item still carries its bullet marker: {line!r}"
    for needle in _NEVER:
        if needle in block.text:
            return f"the block still contains {needle!r}"
    return ""


def _slice(text: str, start: int, end: int) -> slice:
    """A UTF-16 range as a Python slice. Only the check needs this, and only for a message.

    Everything else in this module works in UTF-16 units throughout and never
    converts back. This exists so a complaint can quote the line it is about.
    """
    encoded = text.encode("utf-16-le")
    prefix = encoded[: start * 2].decode("utf-16-le")
    body = encoded[start * 2 : end * 2].decode("utf-16-le")
    return slice(len(prefix), len(prefix) + len(body))


# --- Requests ---------------------------------------------------------------

_RESET_FIELDS = "bold,italic,underline,strikethrough,fontSize,foregroundColor,weightedFontFamily,link"


def requests_for(
    block: Block, *, tab_id: str, at: int, delete_to: int | None = None
) -> list[dict[str, Any]]:
    """One block as one `batchUpdate` body. Pure dict construction.

    `at` is where the block goes; `delete_to` is the end of what it replaces, for
    a re-render. **Delete and insert in the same batch**, which is what keeps
    "one `batchUpdate` per block" true for a replacement as well as an insert:
    after `[at, delete_to)` is removed, index `at` is exactly where the following
    content begins, so the insert needs no adjustment and there is never a moment
    when the old block is gone and the new one is not.

    The order inside the batch is load-bearing:

    1. the delete, if any;
    2. one `insertText` for the block's whole text -- one, not one per span,
       which is the reason styling is a separate pass at all;
    3. a **style reset** over everything just inserted;
    4. paragraph styles, one request per run of same-kind paragraphs;
    5. character styles, one per span;
    6. `createParagraphBullets`, last.

    **Step 3 is not in the specification and is not optional.** `insertText`
    inherits the character and paragraph style at the insertion point, so a block
    inserted immediately before an existing anchor arrives small and gray, and
    one inserted after a heading arrives as a heading. Resetting first and
    styling second makes the result independent of what happened to be there.

    **Step 6 is last, and it is only safe because the subset has one bullet
    level.** The Docs API strips leading tabs when it applies bullets -- that is
    how it reads nesting -- and stripping a character moves every index after it,
    which would invalidate the ranges this batch has already computed. There are
    no tabs to strip, so nothing moves. Anything that ever adds nested bullets
    has to revisit this, which is why it is written here rather than assumed.
    """
    requests: list[dict[str, Any]] = []

    def span_range(start: int, end: int) -> dict[str, Any]:
        return {"tabId": tab_id, "startIndex": at + start, "endIndex": at + end}

    if delete_to is not None and delete_to > at:
        requests.append(
            {"deleteContentRange": {"range": {"tabId": tab_id, "startIndex": at, "endIndex": delete_to}}}
        )

    requests.append(
        {"insertText": {"location": {"tabId": tab_id, "index": at}, "text": block.text}}
    )
    requests.append(
        {
            "updateTextStyle": {
                "range": span_range(0, block.width),
                "textStyle": {},
                "fields": _RESET_FIELDS,
            }
        }
    )

    for start, end, kind in _runs(block.paras):
        requests.append(
            {
                "updateParagraphStyle": {
                    "range": span_range(start, end),
                    "paragraphStyle": {"namedStyleType": NAMED_STYLES[kind]},
                    "fields": "namedStyleType",
                }
            }
        )

    for span in block.spans:
        style, fields = _text_style(span)
        requests.append(
            {
                "updateTextStyle": {
                    "range": span_range(span.start, span.end),
                    "textStyle": style,
                    "fields": fields,
                }
            }
        )

    for start, end, kind in _runs(block.paras):
        if kind == ITEM:
            requests.append(
                {
                    "createParagraphBullets": {
                        "range": span_range(start, end),
                        "bulletPreset": "BULLET_DISC_CIRCLE_SQUARE",
                    }
                }
            )

    return requests


def _runs(paras: list[Para]) -> list[tuple[int, int, str]]:
    """Consecutive paragraphs of one kind, merged. One request per run rather than per paragraph."""
    runs: list[tuple[int, int, str]] = []
    for para in paras:
        if runs and runs[-1][2] == para.kind and runs[-1][1] == para.start:
            runs[-1] = (runs[-1][0], para.end, para.kind)
        else:
            runs.append((para.start, para.end, para.kind))
    return runs


def _text_style(span: Span) -> tuple[dict[str, Any], str]:
    if span.kind == "bold":
        return {"bold": True}, "bold"
    if span.kind == "italic":
        return {"italic": True}, "italic"
    if span.kind == "code":
        return {"weightedFontFamily": {"fontFamily": "Roboto Mono"}}, "weightedFontFamily"
    if span.kind == "link":
        return {"link": {"url": span.url}}, "link"
    if span.kind == "anchor":
        return (
            {
                "fontSize": {"magnitude": ANCHOR_POINTS, "unit": "PT"},
                "foregroundColor": ANCHOR_GRAY,
            },
            "fontSize,foregroundColor",
        )
    raise DigestError(f"unknown span kind {span.kind!r}")


# --- Reading the doc back ---------------------------------------------------


@dataclass(frozen=True)
class Anchor:
    """One block already in the tab: which meeting, and the range it occupies."""

    meeting_id: str
    start: int
    end: int = 0


def scan_anchors(content: list[dict[str, Any]], body_end: int) -> list[Anchor]:
    """Every block in one tab, in document order, with the extent of each.

    Pure over the `body.content` list a `documents.get` returns, so the diff can
    be exercised against a hand-built response.

    A block runs from its anchor to the **next anchor's start**, or to `body_end`
    for the last one -- so anything a person wrote underneath a block belongs to
    that block and is replaced with it. That is deliberate and is the honest
    reading of what a block is: the alternative, stopping at some heuristic end,
    would leave orphaned prose behind after a re-render.
    """
    found: list[Anchor] = []
    for element in content:
        paragraph = element.get("paragraph")
        if not isinstance(paragraph, dict):
            continue
        text = "".join(
            run.get("textRun", {}).get("content", "")
            for run in paragraph.get("elements", [])
            if isinstance(run, dict)
        )
        match = ANCHOR_RE.match(text.strip())
        if match is not None:
            found.append(Anchor(match.group(1), int(element.get("startIndex", 0))))
    found.sort(key=lambda a: a.start)
    return [
        Anchor(a.meeting_id, a.start, found[i + 1].start if i + 1 < len(found) else body_end)
        for i, a in enumerate(found)
    ]


# --- The diff ---------------------------------------------------------------

INSERT, REPLACE, DELETE = "insert", "replace", "delete"


@dataclass(frozen=True)
class Op:
    """One block operation: what to do, to which meeting, over which range."""

    kind: str
    meeting_id: str
    at: int
    end: int = 0


@dataclass(frozen=True)
class Plan:
    """What one sync will do to one tab."""

    ops: list[Op] = field(default_factory=list)
    orphans: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.ops)


def plan(
    anchors: list[Anchor],
    wanted: dict[str, str],
    stored: dict[str, str],
    body_end: int,
    *,
    prune: bool = False,
) -> Plan:
    """Reconcile one tab against the meetings that belong in it.

    `wanted` maps meeting id to the current sha of its `notes.md`; `stored` maps
    meeting id to the sha recorded in its `meta.json` for **this** doc. Three
    outcomes, and the third is the one with an opinion in it:

    * **missing** -- no anchor for a wanted meeting. Insert it in date order,
      which is id order: `YYYY-MM-DD_HHMM` sorts chronologically by
      construction, so nothing here parses a date.
    * **stale** -- the shas differ, or nothing was stored at all. Re-render in
      place. A missing `stored` entry counts as stale rather than as current,
      because it means the doc was written by another copy of Referat or the
      `meta.json` was lost, and re-rendering is the answer that converges.
    * **orphan** -- an anchor for a meeting that no longer carries this tag.
      **Reported and left alone.** The doc may be shared and somebody may have
      written around that block, so removing it is an explicit `--prune` rather
      than a tidy-up nobody asked for.

    **The ops come back sorted descending, and that is the invariant the whole
    step turns on.** Every insert and delete shifts every index after it, so
    working back to front is what keeps the indices from the single
    `documents.get` valid for the whole sync. The sort key is
    `(at, meeting_id)` and the second half is not decoration: two missing
    meetings can land at the same index, and applying the later-sorting one
    first is what leaves them in date order afterwards. It is asserted rather
    than trusted.
    """
    by_id = {a.meeting_id: a for a in anchors}
    ops: list[Op] = []
    orphans: list[str] = []

    for meeting_id in sorted(wanted):
        anchor = by_id.get(meeting_id)
        if anchor is None:
            following = [a.start for a in anchors if a.meeting_id > meeting_id]
            ops.append(Op(INSERT, meeting_id, min(following) if following else body_end))
        elif stored.get(meeting_id) != wanted[meeting_id]:
            ops.append(Op(REPLACE, meeting_id, anchor.start, anchor.end))

    for anchor in anchors:
        if anchor.meeting_id not in wanted:
            orphans.append(anchor.meeting_id)
            if prune:
                ops.append(Op(DELETE, anchor.meeting_id, anchor.start, anchor.end))

    ops.sort(key=lambda op: (op.at, op.meeting_id), reverse=True)
    _assert_descending(ops)
    return Plan(ops, orphans)


def _assert_descending(ops: list[Op]) -> None:
    """The reverse-order invariant, checked where it is produced rather than hoped for.

    `TODO.md` calls applying these in the wrong order the single most likely
    thing in build step 13 to be gotten wrong, and the symptom would be a
    document quietly corrupted rather than an error -- so it is an assertion in
    the function that decides the order, not a comment above the loop that
    applies them.
    """
    for earlier, later in zip(ops, ops[1:]):
        if later.at > earlier.at:
            raise DigestError(f"plan is not in reverse document order: {ops}")
        if later.end and later.end > earlier.at:
            raise DigestError(f"plan has overlapping ranges: {earlier} and {later}")


# --- Staleness --------------------------------------------------------------


def notes_sha256(path: Path) -> str:
    """The sha of a `notes.md`, or `""` when there is none.

    Of the bytes rather than of the parsed content: what is being asked is
    *has this file changed since the block was written*, and a normalization
    would make a re-cleanup that only re-wrapped a line look like no change --
    which is the opposite of what `referat.actions.action_key` wants and right
    for exactly the opposite reason. There a stable key survives a re-wrap; here
    a re-wrap really does mean the doc holds something else.
    """
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def is_synced(meeting: Meeting, db: ProjectsDB) -> bool:
    """Every doc of every project this meeting carries holds its current notes.

    The predicate behind the `synced` lifecycle state, derived on every read and
    stored nowhere -- the same rule `people.directory` runs under, and for the
    same reason: a stored flag would be a second place for the truth to live and
    the first one to disagree with `meta.json`.

    Two cases that look like edge cases and are the rule applied straight.
    An **orphaned** tag contributes no docs and is vacuously satisfied, because
    the project record being gone says nothing about whether the notes were
    pushed. An **archived** project's docs count exactly as a live one's:
    archiving hides a project from the tag picker and changes nothing else, and
    a digest that quietly stopped being kept current would be archiving costing
    something real.
    """
    sha = notes_sha256(meeting.dir / paths.NOTES_MD)
    if not sha:
        return False
    for project_id in meeting.tags:
        project = db.projects.get(project_id)
        if project is None:
            continue
        for doc in project.docs:
            if (meeting.digest.get(doc.gdoc_id) or {}).get("notes_sha256") != sha:
                return False
    return True


def record(meeting: Meeting, gdoc_id: str, tab_id: str, sha: str) -> None:
    """Write down that this meeting's notes now sit in this doc. Does not save."""
    meeting.digest[gdoc_id] = {
        "tab_id": tab_id,
        "notes_sha256": sha,
        "written_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
