r"""Action items: the grammar `/cleanup` writes, and what has been done about them.

Every `notes.md` carries a `## Action items` section, and until now nothing read
it. Sixty-four of them were sitting in nine meetings — eleven of them the
owner's — with no surface on this machine that would ever raise them again. This
module is the parser for that section and the store for what has been done about
what it finds.

**They are parsed, never re-extracted.** `/cleanup` already decided what an
action item is and wrote it down in a consistent grammar; a second LLM pass would
spend a rate-limited subprocess per meeting reproducing that list, and would be a
second implementation of *what an action item is*. Audited before it was chosen:
across all nine files then on disk, **64 items and no deviations** from
`- **owner(s)** — text` — every one leading with a bold span and containing an em
dash. That makes this a parse rather than a guess, which is the only reason it is
allowed to exist: `notes.md` is a *derived* artifact, so reading it does not
breach *nothing is inferred from a transcript*, and nothing here tags a meeting,
proposes a tag, or reorders any queue.

**Nothing here writes `notes.md`, and that is the whole shape of the design.**
The note is the record of what the meeting said. Ticking an item, correcting its
wording and dropping it are facts about *the reader*, and they live in
`<meetings_dir>/actions.json` — so dropping an item removes it from a list and
never from the note, and a `/cleanup` re-run needs no merge because there is
nothing of ours in the file it overwrites.

**An item's identity is its wording**, which is what makes that possible and is
also this design's one real cost. The key is a hash of the note's own sentence,
never of a correction typed over it, so an edit is stable and revertible. A
`/cleanup` re-run that merely *re-wraps* an item keeps its key, because the
normalization collapses whitespace; one that genuinely re-words it does not, and
the tick is orphaned. Orphans are kept and shown rather than pruned — see
:class:`ActionsDB` — because state that vanishes quietly is state you stop
trusting, which is the same reason a deleted project's tags are rendered as
orphans instead of hidden.

**The timestamp patterns here are not :data:`referat.ui.viewer.TIMESTAMP_RE` and
must not be merged with it.** That one finds a *standalone* `[HH:MM:SS]` anywhere
in a note in order to turn it into a link; :data:`TIMESTAMP_RUN_RE` matches a
*trailing run* of them, with its leading comma and its en-dash ranges, in order
to delete it. Two different questions about the same atom, kept apart for exactly
the reason :data:`referat.transcribe.ENTRY_RE` and `ENTRY_PARSE_RE` are. Merging
them would also put `numpy` behind `referat actions`, since that module imports it.

Light on purpose: `re`, `hashlib`, `json` and the stdlib. Nothing here imports Qt,
nothing imports :mod:`referat.cli`, and the mutators are **primitives that save
nothing** — what makes a change correct is the unreadable-file guard, the key
resolution and the single save, and those live in :mod:`referat.cli` where every
surface reaches them. The same split :mod:`referat.projects` states for itself.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from referat import paths

if TYPE_CHECKING:
    from referat.config import Config

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
"""`actions.json`'s own version, so a later shape can be migrated rather than guessed."""

SECTION_RE = re.compile(r"^##\s+action items\s*$", re.IGNORECASE)
"""The heading this module reads, and the only part of a note it looks at.

Matched case-insensitively and with loose spacing because the heading is written
by a language model against a prompt, not by code. A note with no such heading is
**zero items and never an error**: `cleanup.md` tells the pass to drop a section
that would be empty, so a meeting where nothing was assigned genuinely has none.
"""

HEADING_RE = re.compile(r"^##\s")
"""Where the section ends. `###` subsections belong to it; a `##` starts the next one."""

EM_DASH = "—"
SPLIT = f" {EM_DASH} "
"""What separates the owner from the item: space, em dash, space.

U+2014. :data:`TIMESTAMP_RUN_RE` joins a range with U+2013, one code point away,
and the two are impossible to tell apart by eye in a terminal — so they are named
rather than typed literally anywhere below.
"""

UNASSIGNED = "Unassigned"
"""What `/cleanup` writes when the meeting named no owner. Not a person, and not a name.

Mirrors :data:`referat.projects.UNTAGGED`: the computed state of an empty list,
rendered as a word so a reader sees it, and never stored as an owner.
"""

BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
WIKILINK_RE = re.compile(r"\[\[(.+?)\]\]")
OWNER_SPLIT_RE = re.compile(r",|\band\b")

_STAMP = r"\[\d{2}:\d{2}:\d{2}\]"
TIMESTAMP_RUN_RE = re.compile(rf"(?:\s*,)?\s*{_STAMP}(?:\s*[–—-]\s*{_STAMP})?")
"""One trailing timestamp citation, with the comma that joins it to the last one.

The optional second half is a **range** — `[00:28:02]-[00:28:23]`, three of which
are in `2026-09-03_1004` — whose separator is an en dash. Both halves have to go
or the leftovers read as part of the sentence.
"""

TIMESTAMP_RE = re.compile(r"\[(\d{2}):(\d{2}):(\d{2})\]")
"""One `[HH:MM:SS]`, captured, for turning a citation into seconds."""

NOISE_RE = re.compile(r"^[,\s]*(?:and[,\s]*)*$")
"""A head remainder that is only separators, once the bold spans are removed.

`**A**, **B**, **C**` leaves `, ,` and `**A** and **B**` leaves `and`; neither is
a qualifier. Anything else is — see :func:`parse_actions`.
"""

TIDY_RE = re.compile(r"\s+([.;,])")

BARE_DUE_RE = re.compile(r",\s*(\d{4}-\d{2}-\d{2})\s*\.?$")
"""A date as the item's own last clause: `..., 2026-09-07`."""

CUE_DUE_RE = re.compile(
    r"\b(?:by|before|due|deadline|on)\b((?:\s+[A-Za-z]+){0,2})\s+(\d{4}-\d{2}-\d{2})"
)
"""A date introduced by a word that makes it a deadline.

**This is deliberately not "any `YYYY-MM-DD` in the sentence", and the difference
was measured.** That simpler rule gets five of sixty-four wrong, and every one of
them wrong in the direction that matters — a date shown as a deadline that is not
one:

- *"explicitly deferred until after 2026-09-04"* — the item is deferred **past** it
- *"ask [[Niklas]] for help at the 2026-09-09 meeting"* — a meeting date, and the
  fallback rather than the deadline
- *"settle author order at the 2026-09-09 meeting"* — the same
- *"ask [[Raymond]] this afternoon (2026-09-02)"* — a parenthetical gloss
- *"starting Friday morning 2026-09-04 at the latest"* — a **start** date

The cue list is therefore short and does not include `at`, `after` or `starting`.
Up to two purely alphabetic tokens may stand between the cue and the date, which
real items need — `by midday 2026-09-03`, `before Monday 2026-09-07` — and no
comma may, which is what stops `before writing starts, 2026-09-07` from matching
through the clause boundary. Measured over the corpus: **13 due dates, all
correct, no false positives and no false negatives.**

Prose dates never become a `due`. *"weekend or Monday"*, *"no date"* and *"ahead
of Monday"* stay in the text where a person reads them, because a guessed
deadline is the same failure as a guessed name.
"""

NEGATION_RE = re.compile(r"(?:until\s+after|not\s+until|after)\s*$", re.IGNORECASE)
"""Belt and braces over :data:`CUE_DUE_RE`, whose cue list already excludes these.

Kept because the cue list is the sort of thing somebody widens later — adding
`on` was already tempting enough to be worth the guard standing behind it.
"""


def tidy(text: str) -> str:
    """Collapse whitespace and pull punctuation back onto the word before it.

    Run after a timestamp citation is cut out, which leaves double spaces and
    orphaned gaps before a full stop. **Never a blanket comma strip**, which is
    the obvious next step and would collapse `...before writing starts, 2026-09-07`
    into something :data:`BARE_DUE_RE` no longer matches.
    """
    return TIDY_RE.sub(r"\1", " ".join(text.split())).strip()


def _strip_wikilinks(text: str) -> str:
    return WIKILINK_RE.sub(r"\1", text)


@dataclass(frozen=True)
class ActionItem:
    """One line of a `## Action items` section, taken apart.

    `text` is the item without its owner and without its timestamp citations, so
    it is what a list renders and what goes on the clipboard. `raw` is the line as
    the note has it, which is what the key is computed from and what an orphan is
    shown as.
    """

    key: str
    meeting: str
    owners: list[str]
    unassigned: bool
    qualifier: str
    """Whatever the owner head said outside its bold spans.

    Two real items carry meaning here — `**[[Helen]]** *(absent)*` and
    `**Sophia and Georg** (external annotators, per [[Vanessa Leung]])` — and the
    second is the only place the note records who those two report to. Dropping it
    would be a silent deletion; collecting *wikilinks* instead of bold spans, the
    other obvious implementation, would have made Vanessa an owner.
    """
    text: str
    due: str
    at: list[float]
    """Every timestamp cited, in seconds. `at[0]` is the one worth navigating to."""
    raw: str

    def owner_text(self) -> str:
        """Who this is for, as a person reads it — `Unassigned` when nobody."""
        if self.unassigned or not self.owners:
            return UNASSIGNED
        return ", ".join(self.owners)


def _section(text: str) -> list[str]:
    """The lines of the `## Action items` section, or none at all."""
    lines = text.splitlines()
    for start, line in enumerate(lines):
        if not SECTION_RE.match(line):
            continue
        body: list[str] = []
        for line in lines[start + 1 :]:
            if HEADING_RE.match(line):
                break
            body.append(line)
        return body
    return []


def _bullets(lines: list[str]) -> list[str]:
    """Fold the section's lines into one string per item.

    A new item starts at a **column-zero** `- `; every other non-blank line is a
    continuation and is joined with a single space. Blank lines are skipped rather
    than treated as separators — one opens the section in `2026-09-01_0900`.

    An *indented* `- ` is a continuation too, which is the one case somebody will
    later "fix" the other way. It is right because `cleanup.md` forbids nested
    bullets outright, so an indented dash is a wrapped line that happens to begin
    with one, not a sub-item.
    """
    items: list[str] = []
    for line in lines:
        if not line.strip():
            continue
        if line.startswith("- "):
            items.append(line[2:].strip())
        elif items:
            items[-1] += " " + line.strip()
    return items


def _split_owner(item: str) -> tuple[str, str] | None:
    """`(head, body)` at the first :data:`SPLIT` outside parentheses, after a bold span.

    Anchoring on the bold span is what keeps an em dash used as punctuation later
    in the sentence from being mistaken for the owner separator; the paren-depth
    test does the same for one inside an aside. Verified over the corpus: no head
    contains a second em dash, and every item has both a bold span and a split.
    """
    bold = BOLD_RE.search(item)
    if bold is None:
        return None
    depth = 0
    for i in range(bold.end(), len(item)):
        char = item[i]
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif depth == 0 and item.startswith(SPLIT, i):
            return item[:i], item[i + len(SPLIT) :]
    return None


def _owners(head: str) -> tuple[list[str], bool, str]:
    """`(owners, unassigned, qualifier)` out of the bold head."""
    names: list[str] = []
    for span in BOLD_RE.findall(head):
        for part in OWNER_SPLIT_RE.split(_strip_wikilinks(span)):
            name = part.strip()
            if name:
                names.append(name)
    unassigned = any(name.casefold() == UNASSIGNED.casefold() for name in names)
    if unassigned:
        names = [n for n in names if n.casefold() != UNASSIGNED.casefold()]
    residue = BOLD_RE.sub("", head)
    qualifier = "" if NOISE_RE.match(residue.strip()) else tidy(residue)
    return names, unassigned, qualifier


def _due(text: str) -> str:
    """The deadline this item was given, or empty. See :data:`CUE_DUE_RE`."""
    bare = BARE_DUE_RE.search(text)
    if bare is not None:
        return bare.group(1)
    found = ""
    for match in CUE_DUE_RE.finditer(text):
        if NEGATION_RE.search(text[: match.start()]):
            continue
        found = match.group(2)
    return found


def normalize(text: str) -> str:
    """An item's wording reduced to what its identity depends on.

    Casefolded and whitespace-collapsed, over the text with its timestamps already
    removed. Collapsing whitespace is what makes a `/cleanup` re-run that only
    re-wraps a bullet produce the same key, which is the common case and the one
    worth being stable under.
    """
    return " ".join(text.split()).casefold()


def action_key(meeting_id: str, normalized: str, ordinal: int = 0) -> str:
    """This item's stable id: a hash of the meeting and the note's own wording.

    Of the note's wording and **never of an edited override**, so a correction
    typed over an item does not move it and can always be reverted to what the
    meeting actually said.

    `ordinal` disambiguates the second and later of two identical items in one
    meeting, which is rare but would otherwise make one tick apply to both.
    """
    raw = f"{meeting_id}\x00{normalized}"
    if ordinal:
        raw += f"\x00{ordinal}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def parse_actions(text: str, meeting_id: str) -> list[ActionItem]:
    """Every action item in one `notes.md`, in the order the note lists them.

    Returns `[]` for a note with no such section and for a note that is not there
    at all — both are ordinary, and neither is an error. A bullet this grammar
    cannot take apart is skipped and logged rather than guessed at.
    """
    items: list[ActionItem] = []
    seen: dict[str, int] = {}
    for bullet in _bullets(_section(text)):
        split = _split_owner(bullet)
        if split is None:
            log.debug("%s: not an action item, skipping: %.80s", meeting_id, bullet)
            continue
        head, body = split
        owners, unassigned, qualifier = _owners(head)
        at = [
            float(h) * 3600 + float(m) * 60 + float(s) for h, m, s in TIMESTAMP_RE.findall(body)
        ]
        stripped = tidy(TIMESTAMP_RUN_RE.sub("", body))
        normalized = normalize(f"{head}{SPLIT}{stripped}")
        ordinal = seen.get(normalized, 0)
        seen[normalized] = ordinal + 1
        items.append(
            ActionItem(
                key=action_key(meeting_id, normalized, ordinal),
                meeting=meeting_id,
                owners=owners,
                unassigned=unassigned,
                qualifier=qualifier,
                text=stripped,
                due=_due(stripped),
                at=at,
                raw=bullet,
            )
        )
    return items


_CACHE: dict[Path, tuple[int, int, list[ActionItem]]] = {}
"""Parsed items per `notes.md`, keyed on the file's `(mtime_ns, size)`.

`st_mtime_ns` rather than `st_mtime`, with the size beside it: a note is written
once by a subprocess minutes apart, so at nanosecond resolution a missed
invalidation is not reachable in practice. It lives in the module rather than in
the window so that `referat actions` at the prompt and the command center give
the same answer from the same code.
"""


def read_actions(notes_path: Path, meeting_id: str) -> list[ActionItem]:
    """Parse one meeting's notes, from cache when the file has not moved.

    A missing or unreadable `notes.md` is **zero items and never an error**: a
    staged meeting has none by definition, and a transcript that has not been
    through `/cleanup` yet has none either.
    """
    try:
        stat = notes_path.stat()
    except OSError:
        _CACHE.pop(notes_path, None)
        return []
    stamp = (stat.st_mtime_ns, stat.st_size)
    cached = _CACHE.get(notes_path)
    if cached is not None and cached[:2] == stamp:
        return cached[2]
    try:
        text = notes_path.read_text(encoding="utf-8")
    except OSError:
        log.warning("cannot read %s", notes_path, exc_info=True)
        return []
    items = parse_actions(text, meeting_id)
    _CACHE[notes_path] = (*stamp, items)
    return items


# --- What has been done about them ------------------------------------------


@dataclass
class Entry:
    """One item's state: ticked, dropped, corrected — independently of each other.

    `text` is a correction typed over the note's wording, empty when there is
    none, which is also how reverting works. `seen` is the wording this entry was
    created against, and it is what lets an orphan be *shown* rather than merely
    counted once `/cleanup` has re-worded the item out from under it.
    """

    done: bool = False
    done_at: str = ""
    dismissed: bool = False
    text: str = ""
    seen: str = ""

    def empty(self) -> bool:
        """Nothing has been done about this item, so nothing needs storing."""
        return not (self.done or self.dismissed or self.text)

    def to_json(self) -> dict[str, Any]:
        return {
            "done": self.done,
            "done_at": self.done_at,
            "dismissed": self.dismissed,
            "text": self.text,
            "seen": self.seen,
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> Entry:
        return cls(
            done=bool(raw.get("done", False)),
            done_at=str(raw.get("done_at", "")),
            dismissed=bool(raw.get("dismissed", False)),
            text=str(raw.get("text", "")),
            seen=str(raw.get("seen", "")),
        )


@dataclass
class ActionsDB:
    """`actions.json`, loaded. Keyed by meeting id, then by item key."""

    path: Path
    meetings: dict[str, dict[str, Entry]] = field(default_factory=dict)
    unreadable: bool = False
    """The file is there but could not be parsed, so this object is empty by accident.

    An empty store and an unreadable one look identical from the outside and must
    not be treated identically: reading degrades to *nothing has been done*, while
    **writing** would replace a file full of ticks with `{}`. Every mutating
    caller checks this first. A missing file is not unreadable — that is the
    ordinary state before the first tick.

    Reading degrading this way has a second consequence worth stating, because it
    is the opposite of harmless: with no state, every item reads as *open*, so a
    broken file makes the dashboard's box **grow**. That is why
    :func:`referat.cli.actions_document` carries the complaint and every surface
    shows it rather than quietly rendering a longer list.
    """

    @classmethod
    def load(cls, config: Config) -> ActionsDB:
        """Read the store. **Never raises** — an unreadable one comes back empty.

        The same rule :meth:`referat.projects.ProjectsDB.load` runs under. A
        malformed `actions.json` may cost the record of what has been done. It may
        never cost an action item, because the items are in the notes.
        """
        path = actions_path(config)
        db = cls(path=path)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return db
        except (OSError, json.JSONDecodeError):
            log.warning("cannot read the actions file at %s", path, exc_info=True)
            db.unreadable = True
            return db
        if not isinstance(raw, dict):
            log.warning("ignoring malformed %s", path)
            db.unreadable = True
            return db

        for mid, entries in (raw.get("meetings") or {}).items():
            if not isinstance(entries, dict):
                continue
            for key, entry in entries.items():
                if isinstance(entry, dict):
                    db.meetings.setdefault(str(mid), {})[str(key)] = Entry.from_json(entry)
        log.debug("loaded action state for %d meeting(s) from %s", len(db.meetings), path)
        return db

    def save(self) -> None:
        """Write the file atomically, rewritten whole.

        Callers must have refused on :attr:`unreadable` first: this writes what is
        in memory, and after a failed parse that is nothing.

        Entries that record nothing are dropped on the way out, so ticking an item
        and unticking it again leaves the file as it was rather than growing it by
        a row of falses.
        """
        payload: dict[str, Any] = {
            "version": SCHEMA_VERSION,
            "meetings": {
                mid: kept
                for mid, entries in sorted(self.meetings.items())
                if (kept := {k: e.to_json() for k, e in sorted(entries.items()) if not e.empty()})
            },
        }
        paths.write_json_atomic(self.path, payload)

    def get(self, meeting_id: str, key: str) -> Entry:
        """This item's state, or a blank one. Never stores what it hands back."""
        return self.meetings.get(meeting_id, {}).get(key, Entry())

    def entry(self, meeting_id: str, key: str) -> Entry:
        """This item's state, created and stored empty if it had none."""
        return self.meetings.setdefault(meeting_id, {}).setdefault(key, Entry())

    def known(self) -> set[tuple[str, str]]:
        """Every `(meeting, key)` this file has an opinion about."""
        return {(mid, key) for mid, entries in self.meetings.items() for key in entries}

    def drop(self, meeting_id: str, key: str) -> bool:
        """Forget an item's state entirely. True when there was some. Saves nothing."""
        entries = self.meetings.get(meeting_id)
        if not entries or key not in entries:
            return False
        del entries[key]
        if not entries:
            del self.meetings[meeting_id]
        return True


def actions_path(config: Config) -> Path:
    """`<meetings_dir>/actions.json`.

    Derived here rather than at each call site, exactly as
    :func:`referat.projects.projects_path` is: one answer to where the state is,
    and one place to change it.
    """
    return config.paths.meetings_dir / paths.ACTIONS_JSON
