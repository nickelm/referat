r"""Projects: the labels a meeting carries, and the file that defines them.

A project is a **thread of work**, not a container. A meeting has zero or more of
them, Gmail-style, and *untagged* is the computed state of an empty list rather
than a project called "untagged" — so nothing has to be tagged, and nothing is
ever tagged on anybody's behalf.

`<meetings_dir>/projects.json` holds the list, beside `.voices/` and the
generated `INDEX.md`, so the meetings folder stays self-describing: everything
about a meeting is reachable from the one path anything already has.

**JSON, and owned here rather than by :mod:`referat.config`.** `config.py`
promises that Referat parses TOML and never writes it back, so hand edits and
comments in `config.toml` survive; this file is machine-written and rewritten
whole on every mutation. Making it TOML would have meant managing that tension
with a header comment warning that comments do not survive. JSON drops it
instead: it goes through the :func:`referat.paths.write_json_atomic` that already
exists, and nobody expects comments in it.

**The id is a slug made from the name at creation and never changes.** A rename
changes `name` and nothing else. That is the entire reason `meta.json` stores
ids: a rename touches this file alone, and no meeting record, no transcript and
no digest anchor is disturbed by it.

**Deleting a project orphans its tags, visibly.** :meth:`ProjectsDB.remove`
touches no `meta.json`, no `notes.md` and no Google Doc. The ids left behind
resolve to nothing and every surface renders them as orphans rather than hiding
them, because a tag quietly vanishing off three meetings is how you lose track of
what a meeting was about.

**Archiving is the answer to a project that is finished rather than a mistake**,
and it is a *presentation* decision and the whole of it. `archived_at` hides a
project from the surfaces that offer one — the tag picker above all — and changes
nothing else: the entry stays here, every meeting keeps its tag, that tag still
resolves through :meth:`ProjectsDB.name_map`, the `glossary` still feeds the
hotword list, and `referat tag` will still add it. Deleting is what loses
something; archiving is what does not, which is why it is a button and a delete
is a modal.

Light on purpose — JSON and string handling, no optional extra. `project add`,
`rename`, `rm`, `describe`, `glossary`, `archive`, `unarchive` and `list`, and
the `tag` / `untag` verbs in :mod:`referat.cli`, are a JSON read and a JSON
write; only build step 13's `link-doc` and `sync` will need the `digest` extra.

**The mutations here are primitives and the CLI owns the operations.** `add`,
`rename`, `remove`, `describe`, `set_glossary` and `set_archived` each change one
field of an in-memory object and save nothing; what makes a change *correct* —
the unreadable-file guard, the name rules, the unknown-id refusal and the single
save — lives in :mod:`referat.cli`, and every surface goes through it. A window
reaching past that into these methods would be the second implementation of all
five.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from referat import paths

if TYPE_CHECKING:
    from referat.config import Config
    from referat.meeting import Meeting

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
"""`projects.json`'s own version, so a later shape can be migrated rather than guessed.

Build step 21 added `archived_at` and deliberately **did not** move it. There was
nothing to migrate: :meth:`Project.from_json` defaults a missing key, so a file
written before that step loads with every project active — which is true of it.
Bumping this would have asserted a migration that does not exist, and nothing
reads the value back anyway. Move it when a key changes *meaning*, not when one
is added.
"""

UNTAGGED = "untagged"
"""The computed state of an empty tag list, and therefore not available as a name.

Reserved for the same reason :data:`referat.voices.RESERVED_NAMES` holds `ME`: a
project literally called "Untagged" would sit in the sidebar beside the *untagged*
filter meaning something else entirely.
"""

_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")


# --- Names and ids ----------------------------------------------------------


def name_complaint(name: str) -> str | None:
    """What is wrong with `name` as a project name, or None when nothing is.

    A sibling of :func:`referat.voices.name_complaint` rather than a call to it,
    which the plan left open either way. Three of that function's four rules are
    about the transcript — `ME` and `REMOTE` are channel labels, `SPEAKER_NN` is
    what an unnamed speaker is already called, and `:` is the transcript's own
    label separator — and none of them mean anything for a thread of work. What a
    project name needs instead is that it survives being turned into an id, and
    that it does not collide with the *absence* of one. Reusing the speaker rules
    would have imported four checks to get the use of none of them.

    Two validators for two different kinds of name is fine. Two validators for the
    same kind is what the plan was guarding against.
    """
    cleaned = " ".join(name.split())
    if not cleaned:
        return "must not be empty"
    if "\n" in name or "\r" in name:
        return "must be one line - it is a display name, not a description"
    if cleaned.lower() == UNTAGGED:
        return f"must not be {cleaned!r}: that is what having no project is called"
    if not slugify(cleaned):
        return "must contain a letter or a digit, so it can be turned into an id"
    return None


def slugify(name: str) -> str:
    """A lowercase, hyphenated ascii id for `name`, or "" when nothing survives.

    Accented letters are folded rather than dropped, so `Målbild` becomes
    `malbild` and not `mlbild`: the id is typed at a prompt and read in a table,
    and a mangled one is worse than a transliterated one.
    """
    folded = unicodedata.normalize("NFKD", name)
    ascii_only = folded.encode("ascii", "ignore").decode("ascii")
    return _SLUG_STRIP_RE.sub("-", ascii_only.lower()).strip("-")


def free_id(base: str, taken: Iterable[str]) -> str:
    """`base`, or `base-2`, `base-3`, ... — the way a colliding meeting folder is suffixed."""
    used = set(taken)
    candidate, n = base, 2
    while candidate in used:
        candidate, n = f"{base}-{n}", n + 1
    return candidate


# --- The file ---------------------------------------------------------------


@dataclass
class DocRef:
    """One Google Doc a project's digest is written into. Build step 13 fills these in.

    **`gdoc_id` and `tab_id` are the identity; `doc_title` and `tab_name` are
    display text.** Neither title is ever used to find anything, which is what
    makes renaming a document or a tab safe and moving one around Drive
    irrelevant — an id is minted once and does not move. They are stored so a
    list can be read without a network call, and corrected by a sync when they
    have changed, rather than going on saying what things were called on the day
    they were linked.

    `tab_id` is stored rather than derived because **every** write has to be
    located by it: a `batchUpdate` request carrying no `tabId` silently targets
    the first tab of the document, which is how a meeting ends up in somebody's
    unrelated notes with no error to notice.
    """

    gdoc_id: str
    tab_id: str = ""
    tab_name: str = ""
    doc_title: str = ""
    linked_at: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "gdoc_id": self.gdoc_id,
            "tab_id": self.tab_id,
            "tab_name": self.tab_name,
            "doc_title": self.doc_title,
            "linked_at": self.linked_at,
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> DocRef | None:
        """One stored doc reference, or None when it has no document to point at."""
        gdoc_id = str(raw.get("gdoc_id", "")).strip()
        if not gdoc_id:
            return None
        return cls(
            gdoc_id=gdoc_id,
            tab_id=str(raw.get("tab_id", "")),
            tab_name=str(raw.get("tab_name", "")),
            doc_title=str(raw.get("doc_title", "")),
            linked_at=str(raw.get("linked_at", "")),
        )


@dataclass
class Project:
    """One thread of work: an immutable id, a display name, and what hangs off it."""

    id: str
    name: str
    docs: list[DocRef] = field(default_factory=list)
    glossary: list[str] = field(default_factory=list)
    """The terms of art, product names and people belonging to this thread of work.

    **The one key in this file that something outside the digests reads, and it is
    read twice at two different times.** `referat.hotwords` merges every project's
    glossary into the one global list handed to faster-whisper, which acts before
    any meeting has been tagged with anything; and once a meeting *is* tagged, the
    glossaries of all its tags are the second list `/cleanup` normalizes that
    meeting's notes against. So it must be readable for a project with no docs
    attached, and nothing here makes it conditional on one.

    Written by `referat project glossary` since build step 20's phase 4, and by
    the command center's projects page through it. Hand edits still work and
    always did: the file is loaded and written back whole.
    """
    description: str = ""
    """One line about this thread of work.

    Reserved for build step 17's notes splitting, and written since phase 4 by
    `referat project describe` — the field arriving before its consumer, which is
    the order it was designed in.
    """
    created_at: str = ""
    auto_sync: bool = True
    """Push this project's meetings into its docs as soon as their notes are written.

    **On by default, and that is a deliberate change to what "only when the user
    asks" means.** Referat is fully offline with two exceptions, both of which
    the Conventions say run only when asked; this makes the second one standing
    rather than per-push. The argument is that linking a document *is* the
    asking, and it is a much more deliberate act than pressing sync afterwards:
    nobody links a project to a digest doc and then wants the doc to be stale.

    What it does **not** widen is what leaves the machine. A sync still only
    touches projects explicitly linked, still only carries meetings explicitly
    tagged, and still sends `notes.md` and nothing else. And it is per project
    rather than global, because the answer differs by document: a doc shared with
    a room full of people is exactly the one somebody wants to read before it
    updates itself.

    Off is the escape hatch and *Sync now* still works while it is off.
    """
    archived_at: str = ""
    """When work on this thread of work stopped, or `""` while it has not.

    **Archiving hides this project and changes nothing else.** It stays in this
    file, every meeting keeps its tag, that tag still resolves to this name
    through :meth:`ProjectsDB.name_map`, this `glossary` still feeds
    :func:`referat.hotwords.collect`, and `referat tag` will still add it. What
    changes is that the tag picker stops offering it and the projects page gives
    it a section of its own.

    A timestamp rather than an `archived: bool`, because `""` is already this
    file's falsy absence — `created_at`, `description`, `DocRef.tab_id` — so
    `bool(project.archived_at)` is the predicate and there is no second field to
    disagree with the first. A flag *beside* a date would be two places saying
    one thing, and a flag *instead* of one throws away the fact worth keeping.
    """

    @property
    def archived(self) -> bool:
        """Whether this project has been archived. The one place emptiness is read."""
        return bool(self.archived_at)

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "docs": [d.to_json() for d in self.docs],
            "glossary": list(self.glossary),
            "description": self.description,
            "created_at": self.created_at,
            "auto_sync": self.auto_sync,
            "archived_at": self.archived_at,
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> Project | None:
        """One stored project, or None when it has no id to be found by.

        Tolerant the way :meth:`referat.meeting.Meeting.load` is: unknown keys are
        ignored and missing ones take their defaults, so a file written by a later
        version still loads.
        """
        pid = str(raw.get("id", "")).strip()
        if not pid:
            return None
        docs = [
            doc
            for entry in raw.get("docs") or []
            if isinstance(entry, dict) and (doc := DocRef.from_json(entry)) is not None
        ]
        glossary = [str(term).strip() for term in raw.get("glossary") or [] if str(term).strip()]
        return cls(
            id=pid,
            name=str(raw.get("name", "")).strip() or pid,
            docs=docs,
            glossary=glossary,
            description=str(raw.get("description", "")),
            created_at=str(raw.get("created_at", "")),
            # Absent means *yes*, so every project written before this key
            # existed reads as auto-syncing -- which is the answer that
            # matches why they were linked. `MeetingStatus` maps its legacy
            # values on load for the same reason: a default is a migration
            # nobody has to run.
            auto_sync=bool(raw.get("auto_sync", True)),
            archived_at=str(raw.get("archived_at", "")),
        )


@dataclass
class ProjectsDB:
    """`projects.json`, loaded. Sorted by id on the way out, so a rewrite is stable."""

    path: Path
    projects: dict[str, Project] = field(default_factory=dict)
    unreadable: bool = False
    """The file is there but could not be parsed, so this object is empty by accident.

    An empty database and an unreadable one look identical from the outside and
    must not be treated identically: reading degrades to no projects, but
    **writing** would replace a file full of them with `[]`. Every mutating caller
    checks this first. A missing file is not unreadable — that is the ordinary
    state before the first `referat project add`.
    """

    @classmethod
    def load(cls, config: Config) -> ProjectsDB:
        """Read the projects file. **Never raises** — an unreadable one comes back empty.

        The same rule the known-voices database runs under, and for a stronger
        reason: build step 12b reads this file from inside the transcription
        pipeline, to merge every glossary into the hotword list. A malformed
        `projects.json` may cost project names and a few hotwords. It may never
        cost a transcript.
        """
        path = projects_path(config)
        db = cls(path=path)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return db
        except (OSError, json.JSONDecodeError):
            log.warning("cannot read the projects file at %s", path, exc_info=True)
            db.unreadable = True
            return db
        if not isinstance(raw, dict):
            log.warning("ignoring malformed %s", path)
            db.unreadable = True
            return db

        for entry in raw.get("projects") or []:
            if not isinstance(entry, dict):
                continue
            project = Project.from_json(entry)
            if project is not None:
                db.projects[project.id] = project
        log.debug("loaded %d project(s) from %s", len(db.projects), path)
        return db

    def save(self) -> None:
        """Write the file atomically, rewritten whole.

        Callers must have refused on :attr:`unreadable` first: this writes what is
        in memory, and after a failed parse that is nothing.
        """
        payload: dict[str, object] = {
            "version": SCHEMA_VERSION,
            "projects": [p.to_json() for p in self.ordered()],
        }
        paths.write_json_atomic(self.path, payload)

    def ordered(self) -> list[Project]:
        """Every project, by id — which is stable, unlike the name."""
        return [self.projects[pid] for pid in sorted(self.projects)]

    def name_map(self) -> dict[str, str]:
        """`{id: display name}` — the join a tag chip needs, and nothing more.

        `referat project list --json` and `referat list --json` both hand this out
        from this one loader, so a surface holding either document renders a
        tag's name the same way and the two cannot come to disagree about it.
        """
        return {p.id: p.name for p in self.ordered()}

    def archived_ids(self) -> set[str]:
        """The projects that have been archived — the set a picker subtracts.

        **Archivedness travels beside the name map and never inside it.** It is
        tempting to make :meth:`name_map` skip these and let every surface hide
        them for free; it would be a bug, and a quiet one. `cli.tags_cell` and
        `referat.ui.rows.tags_text` both render an id *missing* from that map
        with a trailing `?`, because that is what an orphan is — so narrowing it
        would turn every archived project's tags into orphans in `referat list`,
        in `list --json`, in the meetings list, on the dashboard and on the
        people page, all at once. Archiving hides a project from
        the places that *offer* one. It may never unname a tag.
        """
        return {p.id for p in self.projects.values() if p.archived}

    def display(self, pid: str) -> str:
        """A project's name, or the bare id when nothing resolves it — that is an orphan."""
        project = self.projects.get(pid)
        return project.name if project else pid

    # --- Mutations ----------------------------------------------------------

    def add(self, name: str) -> Project:
        """Create a project from a display name. Callers validate with :func:`name_complaint`."""
        cleaned = " ".join(name.split())
        project = Project(
            id=free_id(slugify(cleaned), self.projects),
            name=cleaned,
            created_at=dt.datetime.now().isoformat(timespec="seconds"),
        )
        self.projects[project.id] = project
        return project

    def rename(self, pid: str, name: str) -> Project | None:
        """Change the display name, or None when there is no such project.

        **The id does not move**, which is the whole point of there being one.
        """
        project = self.projects.get(pid)
        if project is None:
            return None
        project.name = " ".join(name.split())
        return project

    def remove(self, pid: str) -> Project | None:
        """Delete a project. Cascades nothing: its tags stay put, as orphans."""
        return self.projects.pop(pid, None)

    def describe(self, pid: str, description: str) -> Project | None:
        """Set the one-line description, or None when there is no such project.

        Collapsed to one line here rather than refused, because a description is
        prose somebody pasted and a newline in it is a formatting accident rather
        than a mistake worth stopping for. A name is the opposite -- it becomes an
        id and is refused outright by :func:`name_complaint`.
        """
        project = self.projects.get(pid)
        if project is None:
            return None
        project.description = " ".join(description.split())
        return project

    def set_glossary(self, pid: str, terms: Iterable[str]) -> Project | None:
        """Replace the glossary wholesale, or None when there is no such project.

        A replacement rather than a diff, which is the opposite of what
        :func:`add_tags` and :func:`remove_tags` are, and the difference is real:
        a tag list is rendered by a picker showing a *subset* of the projects, so
        a replacement there would silently drop whatever the picker failed to
        draw. A glossary is edited as the whole list -- a text box, or a `--add`
        that has just read it -- so the caller has all of it in hand and a
        replacement drops nothing it did not mean to.

        Cleaning is :func:`clean_terms`', so a term reaching `projects.json` is
        already in the shape :func:`referat.hotwords.collect` would have reduced
        it to. Doing it here rather than in the caller keeps a hand-edited file
        and a UI-edited one the same kind of file.
        """
        project = self.projects.get(pid)
        if project is None:
            return None
        project.glossary = clean_terms(terms)
        return project

    def set_archived(self, pid: str, archived: bool) -> Project | None:
        """Archive or unarchive, or None when there is no such project.

        **A second archive does not move the date.** `archived_at` is when work
        on this thread stopped, so re-running the command must not rewrite that
        history -- the same reason `referat rerun` stopped clearing
        `speaker_names`. Unarchiving clears it outright, because a project that
        is live has no date on which it stopped.

        The clock is here beside :meth:`add`'s `created_at` rather than passed
        in, so this file mints its own times in one place.
        """
        project = self.projects.get(pid)
        if project is None:
            return None
        if not archived:
            project.archived_at = ""
        elif not project.archived_at:
            project.archived_at = dt.datetime.now().isoformat(timespec="seconds")
        return project


def clean_terms(terms: Iterable[str]) -> list[str]:
    """Whitespace-collapsed, empties dropped, deduplicated case-insensitively.

    The same reduction :func:`referat.hotwords.collect` applies on the way into
    Whisper's prompt, applied on the way into the file instead. That is not a
    second implementation of anything -- `collect` deduplicates *across* three
    sources and cannot stop deduplicating -- it is the file being written in the
    shape it will be read in, so `referat project glossary` prints what
    `referat hotwords` will use rather than something one term longer.

    First occurrence wins, and its spelling wins with it: a glossary is what a
    term is *called*, so `MacOS` typed after `macOS` is the duplicate rather than
    the correction.
    """
    kept: list[str] = []
    seen: set[str] = set()
    for term in terms:
        cleaned = " ".join(str(term).split())
        key = cleaned.casefold()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        kept.append(cleaned)
    return kept


def projects_path(config: Config) -> Path:
    """`<meetings_dir>/projects.json`.

    Derived here rather than at each call site, the way
    :meth:`referat.config.Config.voices_dir` is: one answer to where the projects
    are, and one place to change it.
    """
    return config.paths.meetings_dir / paths.PROJECTS_JSON


# --- Tags -------------------------------------------------------------------


def tag_counts(meetings: Sequence[Meeting]) -> dict[str, int]:
    """How many of `meetings` carry each project id, orphaned ids included.

    Takes the meetings rather than the config, so a caller that has already loaded
    them — `referat project list` prints them in the same breath — does not load
    them twice.
    """
    counts: dict[str, int] = {}
    for meeting in meetings:
        for pid in meeting.tags:
            counts[pid] = counts.get(pid, 0) + 1
    return counts


def add_tags(meeting: Meeting, ids: Sequence[str]) -> list[str]:
    """Add project ids to a meeting, in order, skipping the ones already on it.

    Returns what was actually added, so an idempotent re-run can say "already
    tagged" rather than claim to have done something.
    """
    added = []
    for pid in ids:
        if pid not in meeting.tags:
            meeting.tags.append(pid)
            added.append(pid)
    return added


def remove_tags(meeting: Meeting, ids: Sequence[str]) -> list[str]:
    """Remove project ids from a meeting. Returns what was actually removed.

    Deliberately asks nothing about whether an id names a real project. An orphan
    left behind by `project rm` is precisely the tag somebody needs to be able to
    take off a meeting, and a check here would make it the one tag they could not.
    """
    wanted = set(ids)
    removed = [pid for pid in meeting.tags if pid in wanted]
    meeting.tags = [pid for pid in meeting.tags if pid not in wanted]
    return removed
