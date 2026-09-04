"""What build step 13's translator does to the real notes, checked without a network.

The dangerous half of the digests is arithmetic — UTF-16 offsets, style ranges
that have to land on the right substring, and block operations whose order is
what keeps stale indices valid. None of that can be exercised by writing into
somebody's Google Doc and looking, which is why `referat/digest.py` is pure and
why this file exists: it runs in the base install, with no `digest` extra, no
credentials and no network, and it exits non-zero on any miss.

The shape is `scripts/bleed_fixture.py`'s — the real corpus where there is one,
a synthetic fixture for the rules the corpus cannot reach, and the result printed
beside the evidence so a wrong answer that looks reasonable is still visible.

    .venv\\Scripts\\python.exe scripts\\digest_report.py
    .venv\\Scripts\\python.exe scripts\\digest_report.py --show 2026-09-03_1408

Seven checks:

1. every real `notes.md` renders, and the acceptance check passes;
2. every style span lands on the substring it claims, sliced back out of the
   UTF-16 encoding rather than out of the Python string;
3. astral characters cost two code units and the common punctuation costs one;
4. the plain text agrees with `referat.ui.richtext`'s, which is the anti-drift
   check — a note pasted into a doc and a note pushed into one must read the
   same;
5. no request in a batch is missing its `tabId`, and the batch is in the order
   `requests_for` documents;
6. the reconciler's plan is applied against a twenty-line simulator of
   `insertText` and `deleteContentRange` and must produce the expected document
   — which is the reverse-order rule tested end to end;
7. `is_synced` says what it should about an orphaned tag and an archived one.
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

# A note holds em dashes and this file's own fixture holds an emoji, and the
# Windows console code page can encode neither -- the same fact `referat
# transcript` records about the document most likely to break a console.
# Rebinding stdout is what lets the fixture print what it is testing.
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from referat import digest, markdown  # noqa: E402
from referat.config import load_config  # noqa: E402
from referat.index import meeting_title  # noqa: E402
from referat.meeting import load_meetings  # noqa: E402

FAILURES: list[str] = []


def fail(message: str) -> None:
    FAILURES.append(message)
    print(f"  MISS  {message}")


def utf16_slice(text: str, start: int, end: int) -> str:
    """The substring a UTF-16 range names, decoded back out of the encoding itself."""
    return text.encode("utf-16-le")[start * 2 : end * 2].decode("utf-16-le")


# --- 1, 2, 4: the real corpus -----------------------------------------------


def check_corpus(meetings) -> None:
    print("\n=== 1-2, 4: the real notes ===\n")
    print(f"{'meeting':22} {'paras':>5} {'spans':>5} {'chars':>6} {'u16':>6}  check")
    rendered = 0
    for meeting in meetings:
        notes = meeting.dir / "notes.md"
        if not notes.exists():
            continue
        rendered += 1
        source = notes.read_text(encoding="utf-8")
        block = digest.render_block(
            meeting.id,
            meeting.started_at.strftime("%Y-%m-%d"),
            meeting_title(meeting),
            source,
        )
        complaint = digest.check_block(block)
        print(
            f"{meeting.id:22} {len(block.paras):5} {len(block.spans):5} "
            f"{len(block.text):6} {block.width:6}  {complaint or 'ok'}"
        )
        if complaint:
            fail(f"{meeting.id}: {complaint}")

        # 2. Every span lands where it says it does.
        for span in block.spans:
            body = utf16_slice(block.text, span.start, span.end)
            if not body:
                fail(f"{meeting.id}: an empty {span.kind} span at {span.start}")
            if "\n" in body:
                # A span may never cross a paragraph: Docs would style the
                # newline and carry the styling into whatever follows it.
                fail(f"{meeting.id}: {span.kind} span crosses a paragraph: {body!r}")
        # Nothing may be left of the emphasis markers. This is what would catch
        # a nested form the grammar mis-parsed into stray asterisks -- measured
        # at zero across the whole corpus, including the one note that wraps a
        # bold run inside an italic one, which the grammar renders wholly italic
        # rather than leaving markup behind.
        if "*" in block.text:
            fail(f"{meeting.id}: {block.text.count('*')} stray asterisk(s) survived")

        # 2b. Every paragraph ends on its newline, and they tile the whole block.
        edge = 0
        for para in block.paras:
            if para.start != edge:
                fail(f"{meeting.id}: paragraphs do not tile at {para.start}")
            if utf16_slice(block.text, para.end - 1, para.end) != "\n":
                fail(f"{meeting.id}: paragraph does not end on its newline")
            edge = para.end
        if edge != block.width:
            fail(f"{meeting.id}: paragraphs stop at {edge}, block is {block.width}")

        # 4. The anti-drift check.
        check_against_richtext(meeting.id, source, block)

    print(f"\n{rendered} notes rendered")
    if rendered == 0:
        fail("no notes.md anywhere — nothing was actually checked")


def check_against_richtext(meeting_id: str, source: str, block: digest.Block) -> None:
    """The digest's plain text must equal what a paste produces. See §4 above."""
    try:
        from referat.ui import richtext
    except Exception as exc:  # pragma: no cover - PySide6 missing is not a miss
        print(f"  (skipping the richtext comparison: {exc})")
        return

    import html
    import re as _re

    html_text = richtext.markdown_html(source)
    # Tags out, entities back, block boundaries to newlines. Crude on purpose:
    # what is being compared is the *characters*, not the structure.
    plain = _re.sub(r"<(li|p|h[1-6])[^>]*>", "\n", html_text)
    plain = _re.sub(r"<[^>]+>", "", plain)
    plain = html.unescape(plain)
    theirs = [line.strip() for line in plain.splitlines() if line.strip()]

    # Ours, minus the two lines the block adds and the H1 it drops.
    mine = [
        utf16_slice(block.text, p.start, p.end).strip()
        for p in block.paras
        if p.kind not in (digest.ANCHOR, digest.TOP)
    ]
    mine = [line for line in mine if line]
    # richtext keeps the H1 that the block deliberately drops.
    if theirs and mine and theirs[0] != mine[0]:
        theirs = theirs[1:]

    if theirs != mine:
        for i, (a, b) in enumerate(zip(theirs, mine)):
            if a != b:
                fail(f"{meeting_id}: paragraph {i} differs from a paste")
                print(f"        paste:  {a[:90]!r}")
                print(f"        digest: {b[:90]!r}")
                break
        else:
            fail(f"{meeting_id}: a paste has {len(theirs)} paragraphs, the digest {len(mine)}")


# --- 3: the offsets ---------------------------------------------------------


def check_offsets() -> None:
    print("\n=== 3: UTF-16 offsets ===\n")
    cases = [
        ("a", "latin a", 1),
        ("—", "em dash", 1),
        ("·", "middle dot", 1),
        ("–", "en dash", 1),
        ("\U0001f600", "grinning face", 2),
        ("\U0001f44d\U0001f3fd", "thumbs up, skin tone", 4),
    ]
    for text, name, expected in cases:
        got = digest.u16(text)
        print(f"  u16({name:22}) = {got:2}, len() = {len(text)}")
        if got != expected:
            fail(f"u16 of the {name} is {got}, expected {expected}")

    source = "😀 and **Anna** after it\n"
    block = digest.render_block("2026-01-01_0900", "2026-01-01", "T", source)
    bold = [s for s in block.spans if s.kind == "bold"]
    if len(bold) != 1:
        fail(f"expected one bold span, got {len(bold)}")
        return
    body = utf16_slice(block.text, bold[0].start, bold[0].end)
    print(f"  emoji fixture: bold span covers {body!r}")
    if body != "Anna":
        fail(f"the emoji shifted the bold span: it covers {body!r}")
    # And the proof that it would have been wrong with len(): the paragraph's
    # own text has one more UTF-16 unit than it has Python characters.
    line = [p for p in block.paras if p.kind == digest.PARAGRAPH][0]
    py = len(utf16_slice(block.text, line.start, line.end))
    if (line.end - line.start) != py + 1:
        fail("the emoji fixture is not actually exercising the difference")


# --- 5: the requests --------------------------------------------------------


def check_requests(meetings) -> None:
    print("\n=== 5: the batch ===\n")
    meeting = next((m for m in meetings if (m.dir / "notes.md").exists()), None)
    if meeting is None:
        return
    block = digest.block_for(meeting)
    requests = digest.requests_for(block, tab_id="t.0", at=1)

    kinds = [next(iter(r)) for r in requests]
    print(f"  {meeting.id}: {len(requests)} requests")
    for kind in dict.fromkeys(kinds):
        print(f"    {kinds.count(kind):4} x {kind}")

    if kinds[0] != "insertText":
        fail(f"the batch does not open with insertText: {kinds[0]}")
    if kinds[1] != "updateTextStyle":
        fail("the style reset is not the second request")
    if "createParagraphBullets" in kinds and kinds[-1] != "createParagraphBullets":
        fail("createParagraphBullets is not last in the batch")

    for request in requests:
        body = next(iter(request.values()))
        target = body.get("range") or body.get("location") or {}
        if not target.get("tabId"):
            fail(f"a {next(iter(request))} request carries no tabId")

    replace = digest.requests_for(block, tab_id="t.0", at=10, delete_to=50)
    if next(iter(replace[0])) != "deleteContentRange":
        fail("a replacement does not open with its delete")
    if len(replace) != len(requests) + 1:
        fail("a replacement is not the insert batch plus one delete")


# --- 6: the reconciler ------------------------------------------------------


def block_text(meeting_id: str) -> str:
    """One block as it exists in the fake document. **The single source of truth.**

    The fixture's first version had the fake `documents.get` response and the
    simulated document build their text separately, so the indices the
    reconciler was given did not describe the string the simulator applied them
    to -- and every failure it reported was the fixture's. One function, called
    by both, is what makes a miss here mean something.
    """
    return (
        digest.ANCHOR_TEMPLATE.format(meeting_id)
        + "\nxxx\n"
        + digest.END_TEMPLATE.format(meeting_id)
        + "\n"
    )


def tab(ids: list[str], *, prose: str = "", legacy: bool = False) -> tuple[list[dict], str, int]:
    """A fake `documents.get` body, the document it describes, and its `body_end`.

    `prose` is a paragraph of somebody else's writing put **after every block**,
    which is the case the closing anchor exists for and which no fixture had
    until real documents turned out to be mostly prose.

    `legacy` writes blocks with no closing anchor, the way they were written
    before 2026-09-04, so the fallback is exercised rather than assumed.

    Indices are 1-based, as the API's are.
    """
    content: list[dict] = []
    document = ""
    index = 1

    def emit(part: str) -> None:
        nonlocal index, document
        content.append(
            {
                "startIndex": index,
                "endIndex": index + len(part),
                "paragraph": {"elements": [{"textRun": {"content": part}}]},
            }
        )
        index += len(part)
        document += part

    for meeting_id in ids:
        emit(digest.ANCHOR_TEMPLATE.format(meeting_id) + "\n")
        emit("xxx\n")
        if not legacy:
            emit(digest.END_TEMPLATE.format(meeting_id) + "\n")
        if prose:
            emit(prose)
    return content, document, index


def simulate(document: str, ops) -> str:
    """Twenty lines of Google Docs, so the ordering rule can be tested for real.

    Applies the plan exactly as :func:`referat.cli.sync_project` does -- in the
    order `plan` returned, against indices taken once and never recomputed --
    which is the only way to find out whether working back to front actually
    keeps them valid.
    """
    for op in ops:
        if op.kind in (digest.REPLACE, digest.DELETE):
            document = document[: op.at - 1] + document[op.end - 1 :]
        if op.kind in (digest.REPLACE, digest.INSERT):
            document = document[: op.at - 1] + block_text(op.meeting_id) + document[op.at - 1 :]
    return document


def check_reconciler() -> None:
    print("\n=== 6: the reconciler ===\n")
    ids = [f"2026-09-0{n}_0900" for n in range(1, 6)]

    def run(name, existing, wanted_ids, current_ids, expected, prune=False):
        """`current_ids` are the blocks whose stored sha still matches; the rest are stale."""
        content, document, body_end = tab(existing)
        anchors = digest.scan_anchors(content, body_end)
        wanted = {i: "sha-new" for i in wanted_ids}
        stored = {i: ("sha-new" if i in current_ids else "sha-old") for i in existing}
        plan = digest.plan(anchors, wanted, stored, body_end, prune=prune)
        got = simulate(document, plan.ops)
        want = "".join(block_text(i) for i in expected)
        ok = got == want
        print(f"  {'ok  ' if ok else 'MISS'} {name}: {len(plan.ops)} op(s), orphans={plan.orphans}")
        if not ok:
            fail(f"{name}: got {got!r}, expected {want!r}")

    run("insert into an empty tab", [], [ids[0]], [], [ids[0]])
    run("insert before the only block", [ids[2]], [ids[0], ids[2]], [ids[2]], [ids[0], ids[2]])
    run("insert after the only block", [ids[0]], [ids[0], ids[2]], [ids[0]], [ids[0], ids[2]])
    run(
        "insert between two",
        [ids[0], ids[3]],
        [ids[0], ids[1], ids[3]],
        [ids[0], ids[3]],
        [ids[0], ids[1], ids[3]],
    )
    run(
        "two inserts at the same index",
        [ids[3]],
        [ids[0], ids[1], ids[3]],
        [ids[3]],
        [ids[0], ids[1], ids[3]],
    )
    run(
        "two inserts at the end",
        [ids[0]],
        [ids[0], ids[2], ids[3]],
        [ids[0]],
        [ids[0], ids[2], ids[3]],
    )
    run("re-render the first block", [ids[0], ids[1]], [ids[0], ids[1]], [ids[1]], [ids[0], ids[1]])
    run("re-render the last block", [ids[0], ids[1]], [ids[0], ids[1]], [ids[0]], [ids[0], ids[1]])
    run("re-render both", [ids[0], ids[1]], [ids[0], ids[1]], [], [ids[0], ids[1]])
    run("an orphan is left alone", [ids[0], ids[1]], [ids[0]], [ids[0], ids[1]], [ids[0], ids[1]])
    run(
        "an orphan is pruned",
        [ids[0], ids[1]],
        [ids[0]],
        [ids[0], ids[1]],
        [ids[0]],
        prune=True,
    )
    run(
        "insert, re-render and prune in one pass",
        [ids[1], ids[3]],
        [ids[0], ids[1], ids[2]],
        [],
        [ids[0], ids[1], ids[2]],
        prune=True,
    )

    # --- The cases the closing anchor exists for ---------------------------
    #
    # These are not edge cases. The documents this was pointed at on the day it
    # shipped are somebody's meeting notes with tabs already named for their
    # contents -- 130,000 characters of prose in one of them -- so "a block with
    # writing underneath it" is the normal case and a tab Referat owns outright
    # is the exception.
    def run_prose(name, existing, wanted_ids, current_ids, expected, *, legacy=False):
        prose = "somebody wrote this\n"
        content, document, body_end = tab(existing, prose=prose, legacy=legacy)
        anchors = digest.scan_anchors(content, body_end)
        wanted = {i: "sha-new" for i in wanted_ids}
        stored = {i: ("sha-new" if i in current_ids else "sha-old") for i in existing}
        plan = digest.plan(anchors, wanted, stored, body_end, prune=False)
        got = simulate(document, plan.ops)
        kept = got.count(prose)
        ok = got == expected(prose) and kept == len(existing)
        print(f"  {'ok  ' if ok else 'MISS'} {name}: {kept}/{len(existing)} prose paragraph(s) survived")
        if not ok:
            fail(f"{name}: got {got!r}")

    run_prose(
        "prose under a re-rendered block survives",
        [ids[0], ids[1]],
        [ids[0], ids[1]],
        [ids[1]],
        lambda pr: block_text(ids[0]) + pr + block_text(ids[1]) + pr,
    )
    run_prose(
        "prose under the LAST block survives a re-render",
        [ids[0], ids[1]],
        [ids[0], ids[1]],
        [ids[0]],
        lambda pr: block_text(ids[0]) + pr + block_text(ids[1]) + pr,
    )
    run_prose(
        "an insert lands between blocks, not inside somebody's paragraph",
        [ids[0], ids[3]],
        [ids[0], ids[1], ids[3]],
        [ids[0], ids[3]],
        lambda pr: block_text(ids[0]) + pr + block_text(ids[1]) + block_text(ids[3]) + pr,
    )

    # A block written before the closing anchor existed. It has no pair, so it
    # falls back to the old rule -- which means it DOES still own the prose under
    # it, once, until that re-render replaces it with a properly closed block.
    content, document, body_end = tab([ids[0]], prose="old prose\n", legacy=True)
    anchors = digest.scan_anchors(content, body_end)
    plan = digest.plan(anchors, {ids[0]: "new"}, {ids[0]: "old"}, body_end)
    got = simulate(document, plan.ops)
    ok = got.startswith(block_text(ids[0])) and digest.END_TEMPLATE.format(ids[0]) in got
    print(f"  {'ok  ' if ok else 'MISS'} a legacy block re-renders into a closed one")
    if not ok:
        fail(f"legacy re-render: got {got!r}")

    # --- newest_first ------------------------------------------------------
    def run_order(name, existing, wanted_ids, expected, *, newest_first):
        content, document, body_end = tab(existing)
        anchors = digest.scan_anchors(content, body_end)
        plan = digest.plan(
            anchors,
            {i: "sha" for i in wanted_ids},
            {i: "sha" for i in existing},
            body_end,
            newest_first=newest_first,
        )
        got = simulate(document, plan.ops)
        want = "".join(block_text(i) for i in expected)
        ok = got == want
        print(f"  {'ok  ' if ok else 'MISS'} {name}"
              f"{' (misordered)' if plan.misordered else ''}")
        if not ok:
            fail(f"{name}: got {got!r}, expected {want!r}")

    run_order(
        "newest first: into an empty tab",
        [], [ids[0], ids[1]], [ids[1], ids[0]], newest_first=True,
    )
    run_order(
        "newest first: a later meeting goes on top",
        [ids[2]], [ids[2], ids[3]], [ids[3], ids[2]], newest_first=True,
    )
    run_order(
        "newest first: an earlier meeting goes underneath",
        [ids[2]], [ids[1], ids[2]], [ids[2], ids[1]], newest_first=True,
    )
    run_order(
        "newest first: three at once, into an empty tab",
        [], [ids[0], ids[1], ids[2]], [ids[2], ids[1], ids[0]], newest_first=True,
    )
    run_order(
        "oldest first is unchanged",
        [ids[1]], [ids[0], ids[1], ids[2]], [ids[0], ids[1], ids[2]], newest_first=False,
    )

    # A doc laid out the other way is reported and never rearranged.
    content, _, body_end = tab([ids[0], ids[1]])
    anchors = digest.scan_anchors(content, body_end)
    p_new = digest.plan(anchors, {i: "s" for i in ids[:2]}, {i: "s" for i in ids[:2]},
                        body_end, newest_first=True)
    p_old = digest.plan(anchors, {i: "s" for i in ids[:2]}, {i: "s" for i in ids[:2]},
                        body_end, newest_first=False)
    ok = p_new.misordered and not p_old.misordered and not p_new.ops
    print(f"  {'ok  ' if ok else 'MISS'} an oldest-first doc is reported, not rearranged")
    if not ok:
        fail("misordered detection: "
             f"newest_first={p_new.misordered}, oldest_first={p_old.misordered}, ops={p_new.ops}")

    # A block with no `digest` entry at all counts as stale rather than current:
    # it means another copy of Referat wrote the doc, or the meta.json was lost.
    content, _, body_end = tab([ids[0]])
    anchors = digest.scan_anchors(content, body_end)
    plan = digest.plan(anchors, {ids[0]: "sha"}, {}, body_end)
    print(f"  {'ok  ' if plan.ops else 'MISS'} an unrecorded block is stale")
    if not plan.ops:
        fail("an anchor with no digest entry was treated as current")


def check_show(meetings, meeting_id: str) -> None:
    meeting = next((m for m in meetings if m.id == meeting_id), None)
    if meeting is None:
        print(f"no such meeting: {meeting_id}")
        return
    block = digest.block_for(meeting)
    print(f"\n=== {meeting.id}: the source beside the result ===\n")
    source = markdown.blocks((meeting.dir / "notes.md").read_text(encoding="utf-8"))
    # The block adds two paragraphs of its own and drops the note's `#` H1, so
    # its Nth paragraph is the note's (N - 2 + 1)th. Aligning them is the whole
    # point of printing them together.
    dropped = 1 if source and source[0][0] == markdown.HEADING and source[0][1].startswith("# ") else 0
    produced = [utf16_slice(block.text, p.start, p.end).rstrip("\n") for p in block.paras]
    for i, para in enumerate(block.paras[:14]):
        index = i - 2 + dropped
        src = source[index][1] if 0 <= index < len(source) else "(added by the block)"
        print(f"  [{para.kind:9}] {produced[i][:78]}")
        print(f"   {'':11}  <- {src[:78]}")
    print(f"\n=== the first 6 requests ===\n")
    import json

    for request in digest.requests_for(block, tab_id="t.0", at=1)[:6]:
        print("  " + json.dumps(request, ensure_ascii=False)[:150])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--show", metavar="MEETING_ID", help="print one block beside its source")
    args = parser.parse_args()

    meetings = load_meetings(load_config())
    if args.show:
        check_show(meetings, args.show)
        return 0

    check_corpus(meetings)
    check_offsets()
    check_requests(meetings)
    check_reconciler()

    print("\n" + "=" * 60)
    if FAILURES:
        print(f"{len(FAILURES)} miss(es):")
        for miss in FAILURES:
            print(f"  - {miss}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
