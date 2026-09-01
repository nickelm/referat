r"""Projects: the labels a meeting carries, and the file that defines them.

A project is a **thread of work**, not a container. A meeting has zero or more of
them, Gmail-style, and *untagged* is the computed state of an empty list rather
than a project called "untagged" — so nothing has to be tagged, and nothing is
ever tagged on anybody's behalf.

`<meetings_dir>/projects.json` holds the list, beside `.voices/` and the
generated `INDEX.md`, so the meetings folder stays self-describing and the VS
Code extension finds it from the one path it already has.

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

Light on purpose — JSON and string handling, no optional extra. `project add`,
`rename`, `rm` and `list`, and the `tag` / `untag` verbs in :mod:`referat.cli`,
are a JSON read and a JSON write; only build step 13's `link-doc` and `sync` will
need the `digest` extra.
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
"""`projects.json`'s own version, so a later shape can be migrated rather than guessed."""

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


def _free_id(base: str, taken: Iterable[str]) -> str:
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

    `tab_id` is stored rather than derived because **every** write has to be
    located by it: a `batchUpdate` request carrying no `tabId` silently targets
    the first tab of the document, which is how a meeting ends up in somebody's
    unrelated notes with no error to notice.
    """

    gdoc_id: str
    tab_id: str = ""
    tab_name: str = ""
    linked_at: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "gdoc_id": self.gdoc_id,
            "tab_id": self.tab_id,
            "tab_name": self.tab_name,
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

    Hand-edited into `projects.json` for now — no verb writes it — which is safe
    because the file is loaded and written back whole.
    """
    description: str = ""
    """One line. Reserved for build step 17's notes splitting; written by nothing yet."""
    created_at: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "docs": [d.to_json() for d in self.docs],
            "glossary": list(self.glossary),
            "description": self.description,
            "created_at": self.created_at,
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
        from this one loader, so the sidebar can render a tag's name without a
        second subprocess and the two cannot come to disagree about it.
        """
        return {p.id: p.name for p in self.ordered()}

    def display(self, pid: str) -> str:
        """A project's name, or the bare id when nothing resolves it — that is an orphan."""
        project = self.projects.get(pid)
        return project.name if project else pid

    # --- Mutations ----------------------------------------------------------

    def add(self, name: str) -> Project:
        """Create a project from a display name. Callers validate with :func:`name_complaint`."""
        cleaned = " ".join(name.split())
        project = Project(
            id=_free_id(slugify(cleaned), self.projects),
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
