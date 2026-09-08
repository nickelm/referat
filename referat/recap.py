r"""One project across every meeting that carries its tag, as a brief read once.

Build step 24. The day summary is one day across every project; this is the
same shape turned ninety degrees — one project across every meeting tagged with
it, read at the desk minutes before the recurring meeting it is for. `/recap`
in the meetings folder writes `recaps/<project-id>.md`: `## State`, one short
paragraph on where the project stands, and `## Open`, a brief list of what
needs discussing. This module is everything about that file that is not the
prompt: the bundle the pass reads, the frontmatter it copies, and the
staleness rule a surface shows.

**Which meetings, in what order, and when, are Referat's to say and never the
prompt's.** A meeting's `tags` live in `meta.json`, which nothing in the
meetings folder may read — the division `/standup` already runs under. So
:func:`assemble` writes the tagged notes into one bundle, oldest first, with a
header carrying the project id, the moment of assembly and the ordered meeting
ids, and `/recap` copies that header into the recap's frontmatter rather than
working any of it out. :func:`parse` reads it back.

**Stale is derived on every read and stored nowhere.** No `meta.json` key, no
lifecycle state, nothing inferred from which files exist. A recap is stale when
a meeting tagged with the project is missing from its `meetings`, when one of
those meetings' `notes.md` has changed since, or when a meeting the recap
folded in no longer carries the tag. A stale recap is still shown, with the
reasons beside it, and is never deleted: it is the last brief anybody wrote,
and the button that rewrites it is next to it.

**Changed is decided by sha and never by mtime.** `TODO.md` left that open;
it is settled here on the ground step 13 settled it on: the meetings folder is
in Dropbox, a sync client rewrites modification times, and the digest already
answers *did this note change* with `notes_sha256`. So the frontmatter carries
a `notes` map of meeting id to the sha of its `notes.md` at assembly, and the
`generated` timestamp is for a person to read and is compared against nothing.
The stored sha may be a prefix of the real one — the prompt copies it by hand,
and a value it truncated is still a value that says whether the note changed.

**Regenerated from scratch every time.** The previous recap is not folded into
the new one; every pass reads every note in the series. Folding is the deferred
item in `TODO.md`, with its risk named, and it stays deferred until a pass is
actually slow.

**Nothing here leaves the machine.** The file is derived from notes and is not
notes: the digest push does not carry it, and nothing else reads `recaps/`.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from referat import digest, paths
from referat.config import Config

log = logging.getLogger(__name__)

BUNDLE_SUFFIX = ".bundle.md"
"""What the bundle a pass reads is called, beside the recap it writes.

Inside `recaps/` rather than in the system temp folder, so that `/recap
<project-id>` reads a path it can name from the folder it runs in — the same
reason the prompt takes an id rather than a path. It exists only while a pass
runs; :func:`referat.cli.write_recap` removes it in a `finally`.
"""

MARKER = "<!-- meeting {id}: {when}, {duration} -->"
"""The line that opens each meeting's notes inside the bundle.

An HTML comment because it cannot be mistaken for anything a `notes.md` says:
the notes carry their own `#` title and `##` sections, so a Markdown heading
here would be one more of those, and the prompt is told to read this line as
the boundary and nothing else as one.
"""


def recap_path(config: Config, project_id: str) -> Path:
    """`<meetings_dir>/recaps/<project-id>.md`, whether or not it is there yet."""
    return config.paths.meetings_dir / paths.RECAPS_DIR / f"{project_id}.md"


def bundle_path(config: Config, project_id: str) -> Path:
    """Where the bundle for one pass is written, and read from by `/recap`."""
    return config.paths.meetings_dir / paths.RECAPS_DIR / f"{project_id}{BUNDLE_SUFFIX}"


# --- The series -------------------------------------------------------------


@dataclass(frozen=True)
class Entry:
    """One meeting in a project's series: tagged with it, and in the meetings folder.

    `sha256` is `""` for a meeting with no `notes.md`, which is the only way a
    meeting in the series stays out of the bundle: a recap is written from
    notes, and a meeting that has none has nothing to fold in.
    """

    id: str
    dir: Path
    started_at: str
    title: str
    duration: str
    sha256: str

    @property
    def has_notes(self) -> bool:
        return bool(self.sha256)


def series(document: dict[str, Any], project_id: str) -> list[Entry]:
    """Every meeting tagged with the project, oldest first, with its notes sha.

    Over a `list_document` rather than over `Meeting` objects, so a surface
    holding the listing already pays nothing for the tags; the one file opened
    is each `notes.md`, for its sha, which is what the staleness rule compares.

    **Staged meetings are left out**, for the reason :func:`referat.cli.pending`
    leaves them out of the notes queue: `/recap` runs with its working directory
    at the meetings folder and reads what is there, and a meeting still in
    staging is not. It could not have notes anyway, since `/cleanup` cannot
    reach it either.

    The listing is oldest-first already, which is the order a recap reads its
    notes in — later meetings override earlier ones, and that is only true of
    an order somebody was told about.
    """
    entries: list[Entry] = []
    for meeting in document.get("meetings", ()):
        if project_id not in meeting.get("tags", ()) or meeting.get("staged"):
            continue
        folder = Path(meeting["dir"])
        entries.append(
            Entry(
                id=meeting["id"],
                dir=folder,
                started_at=meeting.get("started_at", ""),
                title=meeting.get("title", meeting["id"]),
                duration=meeting.get("duration", ""),
                sha256=digest.notes_sha256(folder / paths.NOTES_MD),
            )
        )
    return entries


# --- The frontmatter --------------------------------------------------------


@dataclass(frozen=True)
class Frontmatter:
    """The four keys at the top of a recap, and at the top of the bundle it came from.

    `meetings` is the watermark: the ordered ids the pass was handed. `notes` is
    the sha of each one's `notes.md` at that moment, keyed by id, and is what
    :func:`stale_reasons` compares. `generated` is for a person and is compared
    against nothing — see the module docstring on why not mtime.
    """

    project: str = ""
    generated: str = ""
    meetings: tuple[str, ...] = ()
    notes: dict[str, str] = field(default_factory=dict)

    def render(self) -> str:
        """The block between the two `---` lines, as the bundle writes it."""
        lines = [
            f"project: {self.project}",
            f"generated: {self.generated}",
            "meetings:",
            *(f"  - {meeting_id}" for meeting_id in self.meetings),
            "notes:",
            *(f"  {meeting_id}: {self.notes.get(meeting_id, '')}" for meeting_id in self.meetings),
        ]
        return "---\n" + "\n".join(lines) + "\n---\n"

    def differs_from(self, other: Frontmatter) -> str:
        """How this frontmatter disagrees with `other`, or `""` when it does not.

        Read after a pass, comparing what `/recap` wrote against what the bundle
        said, so that a copy the prompt got wrong is reported rather than
        silently left to show as stale on every read. A sha is compared as a
        prefix, since a truncated copy is still an answer.
        """
        if self.project != other.project:
            return f"project is {self.project!r}, not {other.project!r}"
        if self.meetings != other.meetings:
            return f"meetings are {list(self.meetings)}, not {list(other.meetings)}"
        for meeting_id in other.meetings:
            mine = self.notes.get(meeting_id, "")
            theirs = other.notes.get(meeting_id, "")
            if not mine or not theirs.startswith(mine):
                return f"the notes sha for {meeting_id} does not match"
        if self.generated != other.generated:
            return f"generated is {self.generated!r}, not {other.generated!r}"
        return ""


def parse(text: str) -> tuple[Frontmatter | None, str]:
    """A recap's frontmatter and the Markdown under it.

    `None` for a file with no frontmatter at all, which a surface reads as *stale
    for no reason it can name* — the file says nothing about what went into it.
    The grammar is the one :meth:`Frontmatter.render` writes and nothing wider:
    `key: value` at the margin, `  - id` under `meetings`, `  id: sha` under
    `notes`. It is Referat's own format read back by Referat, so a YAML library
    would be a dependency spent on four keys. Unknown keys are ignored rather
    than refused, so a prompt that adds a line of its own costs nothing.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, text
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return None, text

    project = generated = ""
    meetings: list[str] = []
    notes: dict[str, str] = {}
    section = ""
    for line in lines[1:end]:
        if not line.strip():
            continue
        if not line[0].isspace():
            key, _, value = line.partition(":")
            key, value = key.strip(), value.strip()
            section = key
            if key == "project":
                project = value
            elif key == "generated":
                generated = value
            continue
        stripped = line.strip()
        if section == "meetings" and stripped.startswith("-"):
            if meeting_id := stripped[1:].strip():
                meetings.append(meeting_id)
        elif section == "notes":
            meeting_id, _, sha = stripped.partition(":")
            if meeting_id.strip():
                notes[meeting_id.strip()] = sha.strip()

    body = "\n".join(lines[end + 1 :]).lstrip("\n")
    return Frontmatter(project, generated, tuple(meetings), notes), body


# --- Staleness --------------------------------------------------------------


def stale_reasons(entries: list[Entry], front: Frontmatter | None, project_id: str) -> list[str]:
    """Why a recap no longer describes its project, in order; empty means current.

    Pure over the series and the frontmatter, so a surface can ask it about a
    listing it already holds. Three ways to be stale, each its own sentence,
    because the reasons are shown and *tagged since* asks for a different next
    step than *notes changed since*:

    - a meeting tagged with the project and holding notes is not in `meetings`;
    - one that is has a `notes.md` whose sha no longer matches;
    - a meeting in `meetings` no longer carries the tag, or has no notes now.

    A frontmatter that is missing, or that names another project, is stale with
    that as the whole reason. A meeting in the series *without* notes is not a
    reason: there is nothing a pass could have read.
    """
    if front is None:
        return ["the recap has no frontmatter, so nothing says what went into it"]
    if front.project != project_id:
        return [f"the recap says it is for {front.project!r}"]
    reasons: list[str] = []
    noted = {entry.id: entry for entry in entries if entry.has_notes}
    for meeting_id, entry in noted.items():
        if meeting_id not in front.meetings:
            reasons.append(f"{meeting_id} is tagged with this project and is not in the recap")
            continue
        recorded = front.notes.get(meeting_id, "")
        if not recorded:
            reasons.append(f"the recap records no notes sha for {meeting_id}")
        elif not entry.sha256.startswith(recorded):
            reasons.append(f"the notes of {meeting_id} have changed since the recap")
    for meeting_id in front.meetings:
        if meeting_id not in noted:
            reasons.append(f"{meeting_id} is in the recap and no longer carries this tag with notes")
    return reasons


# --- The bundle -------------------------------------------------------------


def assemble(
    project_id: str, name: str, entries: list[Entry], *, now: dt.datetime | None = None
) -> tuple[str, Frontmatter]:
    """The bundle `/recap` reads: the frontmatter, then every note, oldest first.

    Only entries with notes are folded in; the caller decides what to say about
    the rest. Each note is opened by a :data:`MARKER` comment naming the meeting,
    its start and its length, and is otherwise copied whole — the bundle is
    each entire `notes.md`, which is what the digest sends today. If step 17's
    sectioned notes are ever built, the section read here is the digest layer's
    partition and never a second reading of the same headings; until then there
    is no section to extract and nothing here waits for one.

    A note that cannot be read is named in the bundle in place of its text, so
    the pass knows a meeting is missing rather than silently seeing fewer.
    """
    stamp = (now or dt.datetime.now()).isoformat(timespec="seconds")
    noted = [entry for entry in entries if entry.has_notes]
    front = Frontmatter(
        project=project_id,
        generated=stamp,
        meetings=tuple(entry.id for entry in noted),
        notes={entry.id: entry.sha256 for entry in noted},
    )
    parts = [
        front.render(),
        "",
        f"# Recap bundle: {name} ({project_id})",
        "",
        f"Assembled by Referat on {stamp} for `/recap {project_id}`. The `notes.md` of "
        f"every meeting tagged with this project follows, **oldest first**, each "
        f"opened by an HTML comment naming the meeting. {len(noted)} meeting"
        f"{'s' if len(noted) != 1 else ''}. Copy the frontmatter at the top of this "
        f"file into the recap exactly as it is.",
        "",
    ]
    for entry in noted:
        when = entry.started_at.replace("T", " ")[:16]
        parts.append("")
        parts.append(MARKER.format(id=entry.id, when=when, duration=entry.duration or "?"))
        parts.append("")
        try:
            parts.append((entry.dir / paths.NOTES_MD).read_text(encoding="utf-8").rstrip())
        except OSError:
            log.warning("could not read the notes of %s for the recap bundle", entry.id, exc_info=True)
            parts.append(f"*The notes of {entry.id} could not be read.*")
        parts.append("")
    return "\n".join(parts).rstrip() + "\n", front
