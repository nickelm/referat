"""The `referat` command line interface.

`config` since build step 1, `label` since 7b, `list`, `rerun` and `status`
since step 8, `devices` since the first USB microphone, `index` with the
meetings folder scaffold at step 10, `project`, `tag`, `untag` and `state`
at step 14, and `promote` at step 15. Between them they are the whole of Referat
that is not the tray: what has been recorded, what still needs a name, what work
each meeting belongs to, what the tray is doing, what it will record with, how to
transcribe a meeting again, how to accept a transcript the quality gate refused,
and how to rebuild the dashboard over all of it.

**The CLI owns every mutation, and is the only implementation of any of it.**
The projects file, the tag logic, the lifecycle vocabulary and the rules about
what a name may be live in Python exactly once. The VS Code extension shells out
to these commands because it is TypeScript and has no other way in; the tray
imports the same functions, because it is already a Python process inside this
package. The rule is one implementation, not one process boundary.

**Light by default.** Only `rerun` needs the `transcribe` extra, and it imports
it inside :func:`referat.rerun.run`, so `list` and `status` answer instantly
without three gigabytes of torch. `project`, `tag`, `untag` and `state` are a
JSON read and a JSON write and need no extra at all — only step 13's `project
link-doc` and `project sync` will need `digest`. `label` and `devices` are
imported here rather than at module scope for the same kind of reason: a broken
sound device should not be able to cost the rest of the CLI.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from dataclasses import asdict
from pathlib import Path
from types import ModuleType
from typing import Any

from referat import __version__, index, paths, projects, status, voices
from referat.config import Config, ConfigError, load_config
from referat.meeting import (
    Meeting,
    MeetingStatus,
    format_duration,
    load_meetings,
    resolve_meeting,
)

NEEDS_CONFIG = (
    "config",
    "delete",
    "devices",
    "index",
    "label",
    "list",
    "project",
    "promote",
    "reflow",
    "relabel",
    "rerun",
    "state",
    "tag",
    "untag",
)
"""`status` is missing from this on purpose: `status.json` lives in
`%LOCALAPPDATA%`, so an unusable `config.toml` must not stop you asking what the
tray is doing — that is exactly the moment you want to ask."""

NOTES_WRITTEN_VERB = "notes-written"
"""What `referat state <id> notes-written` is spelled at the prompt.

Hyphenated here and underscored in `meta.json`: subcommands and their arguments
are hyphenated everywhere else in this CLI, and the stored value is the
:class:`referat.meeting.MeetingStatus` member. Converted in exactly one place, in
:func:`run_state`.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="referat",
        description="Personal offline meeting recorder and transcriber.",
    )
    parser.add_argument("--version", action="version", version=f"referat {__version__}")

    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser("config", help="show the loaded configuration")
    listing = subcommands.add_parser(
        "list",
        help="every meeting: duration, status, and who still needs a name",
        description=(
            "List the meetings folder, oldest first. The UNNAMED column is the "
            "queue for `referat label`; AUDIO says whether the WAVs are still on "
            "disk or were released once the transcript came out clean; TAGS is "
            "the project ids the meeting carries, with a trailing `?` on an id no "
            "project resolves any more."
        ),
    )
    listing.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="emit the same meetings as JSON, for the VS Code extension",
    )
    tray_status = subcommands.add_parser(
        "status",
        help="what the tray app is doing right now",
        description=(
            "Read the status file the tray writes on every transition. Exit code "
            "0 when a tray is running, 1 when none is."
        ),
    )
    tray_status.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="emit the same answer as JSON, for the VS Code extension's status bar",
    )

    subcommands.add_parser(
        "index",
        help="regenerate the meetings folder's INDEX.md dashboard",
        description=(
            "Rewrite <meetings_dir>/INDEX.md: one row per meeting, with the title "
            "taken from the H1 of its notes.md. It is written automatically at the "
            "end of every transcription, so this is for after a /cleanup run has "
            "given a meeting its title. A different file from the repository's own "
            "INDEX.md, and never hand-edited."
        ),
    )

    subcommands.add_parser(
        "devices",
        help="list the audio devices, and which ones [audio] selects",
        description=(
            "Every input device Windows exposes and every WASAPI loopback source, "
            "with the one `[audio].mic_device` and `[audio].loopback_device` "
            "currently resolve to marked `<- config`. Those two keys are substring "
            "matches, and a substring that matches nothing falls back to the "
            "default silently, so this is how you check that the device you meant "
            "is the device that will be recorded."
        ),
    )

    rerun = subcommands.add_parser(
        "rerun",
        help="transcribe a meeting again from the audio it kept",
        description=(
            "Re-run transcription and diarization over a meeting's WAVs, writing "
            "a new transcript.md. Speakers are renumbered and re-identified from "
            "the known-voices database, so names survive but SPEAKER_NN numbers "
            "do not. Needs the `transcribe` extra."
        ),
    )
    rerun.add_argument("meeting_id", help="e.g. 2026-08-27_1400")
    rerun.add_argument(
        "--force",
        action="store_true",
        help="run even while the tray app is transcribing, which means two "
        "models on one GPU",
    )

    promote = subcommands.add_parser(
        "promote",
        help="move a meeting out of staging and into the meetings folder",
        description=(
            "A meeting is recorded in the staging folder and moves into the "
            "meetings folder once its audio is gone, because no WAV may ever "
            "reach a folder a sync client is watching. A meeting whose transcript "
            "failed the quality gate keeps its audio and therefore stays in "
            "staging. This is the off-ramp: --release-audio deletes both WAVs and "
            "then promotes, which is not reversible, and is the way to accept a "
            "gate_failed transcript instead of re-transcribing it. Without the "
            "flag this only moves a meeting whose audio has already gone."
        ),
    )
    promote.add_argument("meeting_id", help="e.g. 2026-08-27_1400")
    promote.add_argument(
        "--release-audio",
        action="store_true",
        dest="release_audio",
        help="delete the WAVs first. They are gone for good, and the transcript "
        "is all that is left of the meeting",
    )

    reflow = subcommands.add_parser(
        "reflow",
        help="put a blank line between the entries of an older transcript",
        description=(
            "Transcripts written before this separator existed have a single "
            "newline between their entries, and a single newline inside a "
            "Markdown block is a soft break - so they render as one unbroken "
            "paragraph. This inserts the blank line that makes each entry its "
            "own block. It changes no word of speech, no timestamp and no "
            "speaker label, it is idempotent, and it writes nothing when there "
            "is nothing to do. With no meeting id it walks every meeting."
        ),
    )
    reflow.add_argument(
        "meeting_id",
        nargs="?",
        help="a single meeting, e.g. 2026-08-27_1400. Omit for all of them",
    )

    relabel = subcommands.add_parser(
        "relabel",
        help="spell the owner's name into transcripts that still say ME",
        description=(
            "ME means the microphone recorded this and nothing attributed it. It "
            "used to also mean the owner, whom the pipeline wrote as ME while "
            "`referat label` wrote their name - so a transcript could carry one "
            "person under two labels. New meetings spell the name out; this "
            "repairs the ones already written, whose audio is gone and which no "
            "rerun can regenerate. It only touches a meeting whose microphone "
            "clustered into nothing but the owner, because everywhere else ME may "
            "be somebody else in the room. It changes no word of speech and no "
            "timestamp, it is idempotent, and with no meeting id it walks every "
            "meeting."
        ),
    )
    relabel.add_argument(
        "meeting_id",
        nargs="?",
        help="a single meeting, e.g. 2026-08-27_1400. Omit for all of them",
    )

    delete = subcommands.add_parser(
        "delete",
        help="delete a meeting and everything in it",
        description=(
            "Remove a meeting folder - transcript, notes, metadata, speaker "
            "snippets and any audio still in it. Not reversible, and refused "
            "while the meeting is being recorded or transcribed. Two things it "
            "does not do: a meeting inside a synced meetings folder is not "
            "really gone, because a sync client keeps deleted files and prior "
            "versions on its own servers for weeks; and the voiceprints this "
            "meeting contributed stay in the known-voices database, where "
            "`referat label --forget <name>` is what removes a person."
        ),
    )
    delete.add_argument("meeting_id", help="e.g. 2026-08-27_1400")
    delete.add_argument(
        "--yes",
        action="store_true",
        dest="assume_yes",
        help="skip the confirmation. Needed by anything without a terminal, "
        "because the prompt answers no on EOF",
    )

    label = subcommands.add_parser(
        "label",
        help="name the speakers identification could not place",
        description=(
            "Play each unidentified speaker's snippets and ask who it was, then "
            "remember that voice for every meeting after this one. With no "
            "meeting id, walks every meeting that still has unknown speakers, "
            "oldest first."
        ),
    )
    label.add_argument("meeting_id", nargs="?", help="a single meeting, e.g. 2026-08-27_1400")
    label.add_argument(
        "--forget",
        metavar="NAME",
        help="delete this person from the known-voices database and revert their "
        "labels to SPEAKER_NN in every transcript",
    )
    # The three flags below are what the VS Code extension's labeling webview
    # drives. They exist because the prompt above cannot be driven from a
    # subprocess: it reads `input()`, and `--forget`'s confirmation answers *no*
    # on EOF, so a caller with no terminal is told "Nothing was deleted."
    label.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="emit this meeting's unnamed speakers, their snippets and the known "
        "names as JSON, instead of prompting",
    )
    label.add_argument(
        "--speaker",
        metavar="SPEAKER_NN",
        help="name this speaker without prompting; needs --name and a meeting id",
    )
    label.add_argument(
        "--name",
        metavar="NAME",
        help="the name to give --speaker",
    )
    label.add_argument(
        "--yes",
        action="store_true",
        help="skip --forget's confirmation. Deleting a person is not reversible",
    )

    _add_project_parser(subcommands)

    tagging = subcommands.add_parser(
        "tag",
        help="add project tags to a meeting",
        description=(
            "Tag a meeting with one or more projects, by id. Idempotent: tagging "
            "a meeting with a project it already carries changes nothing. "
            "Nothing is ever tagged automatically — that is what keeps the "
            "untagged meetings a queue somebody works through rather than a "
            "bucket of quiet mistakes."
        ),
    )
    tagging.add_argument("meeting_id", help="e.g. 2026-08-27_1400")
    tagging.add_argument("project_id", nargs="+", help="one or more project ids")

    untagging = subcommands.add_parser(
        "untag",
        help="remove project tags from a meeting",
        description=(
            "Remove one or more project tags from a meeting. Idempotent, and it "
            "will happily remove an orphaned id left behind by `project rm` — "
            "that is the one tag you would otherwise be unable to get rid of."
        ),
    )
    untagging.add_argument("meeting_id", help="e.g. 2026-08-27_1400")
    untagging.add_argument("project_id", nargs="+", help="one or more project ids")

    state = subcommands.add_parser(
        "state",
        help="record that /cleanup has written a meeting's notes",
        description=(
            "Move a meeting from `transcribed` to `notes_written`. This is the "
            "one lifecycle transition no pipeline can make: the /cleanup pass is "
            "forbidden from touching meta.json, so whoever spawned it says so "
            "here afterwards. It accepts this transition and no other — "
            "`gate_failed` and `transcribed` are written by the pipeline and "
            "`synced` by `project sync`, and a verb that let a caller claim any "
            "state would turn the field from a record into a comment."
        ),
    )
    state.add_argument("meeting_id", help="e.g. 2026-08-27_1400")
    state.add_argument(
        "transition",
        choices=[NOTES_WRITTEN_VERB],
        help="the only transition this verb makes",
    )

    return parser



def _add_project_parser(subcommands: argparse._SubParsersAction) -> None:
    """`referat project <verb>` — a second positional verb, not a second dispatcher.

    Deliberately built as nested subparsers inside the one `argparse` block the
    rest of the CLI uses, and dispatched from the same `if args.command == ...`
    chain. A registry, a verb table or a class per verb would be a second
    mechanism for the sake of four commands that are each a JSON read and a JSON
    write.

    `link-doc`, `unlink-doc` and `sync` are missing on purpose: they arrive with
    the digests at build step 13. A verb that exists and answers "not built yet"
    reads as a bug; argparse listing the four that do exist does not.
    """
    project = subcommands.add_parser(
        "project",
        help="the projects a meeting can be tagged with",
        description=(
            "Projects are labels, not containers: a meeting carries zero or more "
            "of them, and `untagged` is what an empty list is called rather than "
            "a project. A project's id is a slug fixed when it is created and "
            "never changed by a rename, which is why meetings store ids."
        ),
    )
    verbs = project.add_subparsers(dest="verb")

    add = verbs.add_parser("add", help="create a project, and print the id it got")
    add.add_argument("name", help="what a person reads, e.g. \"Kundprojekt Alfa\"")
    add.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="emit the created project as JSON, so a caller learns its id without "
        "guessing at slugify's output or matching on a name two projects can share",
    )

    rename = verbs.add_parser(
        "rename",
        help="change a project's display name; its id does not move",
        description=(
            "Renaming changes the display name and nothing else. The id stays as "
            "it was, so no meeting record, no transcript and no digest anchor is "
            "disturbed by it."
        ),
    )
    rename.add_argument("project_id")
    rename.add_argument("name", help="the new display name")

    remove = verbs.add_parser(
        "rm",
        help="delete a project, leaving its tags behind as orphans",
        description=(
            "Removes the project from projects.json and touches no meta.json, no "
            "notes.md and no Google Doc. Meetings carrying its id keep carrying "
            "it, and every surface renders it as an orphan rather than hiding it "
            "— a tag quietly vanishing off three meetings is how you lose track "
            "of what a meeting was about. `referat untag` takes them off."
        ),
    )
    remove.add_argument("project_id")

    listing = verbs.add_parser("list", help="every project, and how many meetings carry it")
    listing.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="emit the same projects as JSON, for the VS Code extension",
    )


# --- referat list -----------------------------------------------------------


def audio_state(meeting: Meeting) -> str:
    """`released`, `kept`, or `-` for a meeting that never recorded a channel."""
    if meeting.transcription.get("audio_released"):
        return "released"
    if meeting.mic_path.exists() or meeting.system_path.exists():
        return "kept"
    return "-"


def tags_cell(meeting: Meeting, known: dict[str, str]) -> str:
    """A meeting's tags for the text table: the ids, comma-separated, orphans marked.

    **Ids rather than display names**, even though the names read better: the next
    thing typed after reading this column is `referat untag <meeting> <id>`, and a
    table that printed names would be a table you cannot copy out of. The ids are
    slugs of the names anyway. `referat project list` is where the two are joined.

    An id no project resolves is an orphan left behind by a `project rm`, and it
    gets a trailing `?` rather than being dropped — a tag disappearing quietly off
    three meetings is how you lose track of what a meeting was about.
    """
    return ", ".join(pid if pid in known else f"{pid}?" for pid in meeting.tags) or "-"


def list_row(meeting: Meeting, known: dict[str, str]) -> tuple[str, ...]:
    """One line of `referat list`, as its six columns."""
    unnamed = len(voices.unknown_speakers(meeting))
    return (
        meeting.id,
        format_duration(meeting.duration_seconds),
        str(meeting.status),
        audio_state(meeting),
        str(unnamed) if unnamed else "-",
        tags_cell(meeting, known),
    )


HEADERS = ("MEETING", "DURATION", "STATUS", "AUDIO", "UNNAMED", "TAGS")
RIGHT_ALIGNED = (1, 4)
"""Duration and the unnamed count read as numbers; the rest read as words.

TAGS goes last because it is the one column with no bound on its width, and a
ragged column in the middle would push STATUS and AUDIO around meeting by
meeting."""


def render_table(
    rows: list[tuple[str, ...]],
    headers: tuple[str, ...],
    right_aligned: tuple[int, ...] = (),
) -> str:
    """A plain aligned table. ASCII only — the console code page here is not UTF-8."""
    widths = [max(len(str(cell)) for cell in column) for column in zip(headers, *rows)]
    lines = []
    for row in (headers, *rows):
        cells = [
            str(cell).rjust(width) if index in right_aligned else str(cell).ljust(width)
            for index, (cell, width) in enumerate(zip(row, widths))
        ]
        lines.append("  ".join(cells).rstrip())
    return "\n".join(lines)


def list_document(config: Config) -> dict[str, Any]:
    """Every meeting, as the JSON the VS Code extension reads.

    The extension needs six things it would otherwise have to work out for
    itself: the two roots, the duration formatter, `audio_state`, the title rule,
    the staged/promoted distinction, and which speakers are still numbers. All
    six already exist exactly once in Python and are shared by three or more
    callers *so that they cannot disagree* — so the extension is given the
    answers rather than the ingredients. A second implementation in TypeScript
    would be a seventh reader of `meta.json` with its own opinions about all of
    them.

    Deliberately not sorted here: this is `referat list`'s document, so it keeps
    `list`'s oldest-first order, and the dashboard that wants newest-first
    reverses it the way `referat.index` already does.

    The top-level `projects` block is the seventh thing, added at step 14: the map
    from a tag id to what a person reads. It comes out of the same
    `ProjectsDB.name_map` that `referat project list --json` hands out, so this
    one subprocess feeds the whole sidebar and the join between a tag and its name
    cannot drift either. An id in a meeting's `tags` that is missing from this map
    is an orphan, and that absence is the extension's cue to render it as one.
    """
    meetings = load_meetings(config)
    return {
        "meetings_dir": str(config.paths.meetings_dir),
        "staging_dir": str(config.staging_dir()),
        "projects": projects.ProjectsDB.load(config).name_map(),
        "meetings": [
            {
                "id": m.id,
                "dir": str(m.dir),
                "started_at": m.started_at.isoformat(timespec="seconds"),
                "duration_seconds": m.duration_seconds,
                "duration": format_duration(m.duration_seconds),
                "status": str(m.status),
                "audio": audio_state(m),
                "staged": m.dir.parent != config.paths.meetings_dir,
                "title": index.meeting_title(m),
                "transcript": m.transcript_path.exists(),
                "notes": (m.dir / paths.NOTES_MD).exists(),
                "unnamed": voices.unknown_speakers(m),
                "tags": list(m.tags),
            }
            for m in meetings
        ],
    }


def run_list(config: Config, as_json: bool = False) -> int:
    """`referat list`. Oldest first, so the newest meeting lands next to the prompt."""
    if as_json:
        print(json.dumps(list_document(config), indent=2))
        return 0

    meetings = load_meetings(config)
    if not meetings:
        print(f"No meetings in {config.paths.meetings_dir} yet.")
        return 0

    known = projects.ProjectsDB.load(config).name_map()
    rows = [list_row(m, known) for m in meetings]
    print(render_table(rows, HEADERS, RIGHT_ALIGNED))

    pending = sum(1 for row in rows if row[4] != "-")
    summary = f"\n{len(meetings)} meeting{'s' if len(meetings) != 1 else ''}"
    if pending:
        summary += f", {pending} with unnamed speakers - run: referat label"
    staged = sum(1 for m in meetings if m.dir.parent != config.paths.meetings_dir)
    if staged:
        # Still in staging means the audio was kept, so the transcript was not
        # trusted. Say so, or these read as identical to the finished ones.
        summary += f", {staged} still local with audio kept - run: referat rerun"
    print(summary)
    return 0


# --- referat project, tag, untag, state -------------------------------------


def _resolve_meeting(config: Config, meeting_id: str, command: str) -> Meeting | None:
    """One meeting by id, complaining to stderr in the name of the command that asked.

    The lookup and both of its complaints are
    :func:`referat.meeting.resolve_meeting`; all this adds is the prefix, which is
    the only part `tag`, `untag`, `state` and `referat label` disagree about.
    """
    meeting, why = resolve_meeting(config, meeting_id)
    if meeting is None:
        print(f"referat {command}: {why}", file=sys.stderr)
    return meeting


PROJECT_HEADERS = ("ID", "NAME", "MEETINGS", "DOCS")
PROJECT_RIGHT_ALIGNED = (2, 3)


def project_document(config: Config) -> dict[str, Any]:
    """`referat project list --json`: every project, and how much hangs off it.

    `names` is :meth:`referat.projects.ProjectsDB.name_map`, the very map
    :func:`list_document` embeds — so the sidebar's tag chips resolve identically
    whichever of the two documents it happens to be holding.

    `orphans` is the other half of the same question: ids that meetings still
    carry and no project answers to. They are counted here rather than left for
    each reader to derive, which is the whole habit this step is about.
    """
    db = projects.ProjectsDB.load(config)
    counts = projects.tag_counts(load_meetings(config))
    return {
        "projects_file": str(projects.projects_path(config)),
        "names": db.name_map(),
        "projects": [
            {
                **project.to_json(),
                "meetings": counts.get(project.id, 0),
            }
            for project in db.ordered()
        ],
        "orphans": {pid: n for pid, n in sorted(counts.items()) if pid not in db.projects},
    }


def run_project_list(config: Config, as_json: bool = False) -> int:
    """`referat project list`."""
    if as_json:
        print(json.dumps(project_document(config), indent=2))
        return 0

    document = project_document(config)
    entries = document["projects"]
    if not entries:
        print("No projects yet. Create one with: referat project add <name>")
        return 0

    rows = [
        (p["id"], p["name"], str(p["meetings"]), str(len(p["docs"])) if p["docs"] else "-")
        for p in entries
    ]
    print(render_table(rows, PROJECT_HEADERS, PROJECT_RIGHT_ALIGNED))
    print(f"\n{len(rows)} project{'s' if len(rows) != 1 else ''}")

    orphans = document["orphans"]
    if orphans:
        # Named rather than hidden, and named here rather than only in the
        # meetings table: a deleted project leaves its ids behind on purpose, and
        # this is the place somebody would come looking for what they were.
        listed = ", ".join(f"{pid} ({n})" for pid, n in orphans.items())
        print(
            f"{len(orphans)} orphaned tag{'s' if len(orphans) != 1 else ''} on meetings, "
            f"belonging to no project: {listed}"
        )
    return 0


def run_project(config: Config, args: argparse.Namespace) -> int:
    """`referat project <verb>`. Every verb here is a JSON read and a JSON write."""
    if args.verb is None:
        print("referat project: pick a verb - add, rename, rm or list", file=sys.stderr)
        return 2

    if args.verb == "list":
        return run_project_list(config, as_json=args.as_json)

    db = projects.ProjectsDB.load(config)
    if db.unreadable:
        return _unreadable_projects(db, f"project {args.verb}", writing=True)

    if args.verb == "add":
        complaint = projects.name_complaint(args.name)
        if complaint:
            print(f"referat project add: a project name {complaint}", file=sys.stderr)
            return 1
        project = db.add(args.name)
        db.save()
        if getattr(args, "as_json", False):
            # The id is `slugify` plus a collision suffix, so a caller cannot work
            # it out from the name — and matching the name back out of `project
            # list` is wrong whenever two projects share one, which is allowed.
            # The extension had that bug: it created the project and then tagged
            # the meeting with the first project of the same name, or with
            # nothing at all.
            print(json.dumps(project.to_json(), indent=2))
            return 0
        print(f"{project.id}  {project.name}")
        return 0

    if args.verb == "rename":
        complaint = projects.name_complaint(args.name)
        if complaint:
            print(f"referat project rename: a project name {complaint}", file=sys.stderr)
            return 1
        project = db.rename(args.project_id, args.name)
        if project is None:
            return _no_such_project(db, args.project_id, "project rename")
        db.save()
        # Said out loud because the id *not* moving is the surprising half, and it
        # is the half every meeting record depends on.
        print(f"{project.id} is now called {project.name} (the id does not change)")
        return 0

    if args.verb == "rm":
        project = db.remove(args.project_id)
        if project is None:
            return _no_such_project(db, args.project_id, "project rm")
        db.save()
        carrying = projects.tag_counts(load_meetings(config)).get(project.id, 0)
        print(f"Deleted {project.id} ({project.name}).")
        if carrying:
            print(
                f"{carrying} meeting{'s' if carrying != 1 else ''} still carr"
                f"{'y' if carrying != 1 else 'ies'} that id as an orphaned tag; "
                f"nothing else was touched. Remove them with: referat untag <id> "
                f"{project.id}"
            )
        return 0

    print(f"referat project: unknown verb {args.verb}", file=sys.stderr)
    return 2


def _unreadable_projects(db: projects.ProjectsDB, command: str, writing: bool) -> int:
    """Stop a command that cannot tell an empty projects file from an unparseable one.

    An unreadable file loads as *no projects*. That is right for reading — the
    meetings table still renders, with every tag shown as an orphan — and
    catastrophic for writing, because the next save would replace a file full of
    projects with an empty list and nothing would say so. `tag` does not write it
    and is stopped anyway: with no ids loaded it would reject every id as unknown
    and report a typo nobody made.

    The parse error itself is in the log; this says what to do about it.
    """
    consequence = (
        "so writing now would replace everything in it"
        if writing
        else "so nothing can be tagged until it is"
    )
    print(
        f"referat {command}: {db.path} exists but could not be read, "
        f"{consequence}. Fix or delete that file first.",
        file=sys.stderr,
    )
    return 1


def _no_such_project(db: projects.ProjectsDB, pid: str, command: str) -> int:
    """Complain about an id nothing resolves, and say what would have worked."""
    known = ", ".join(sorted(db.projects)) or "none yet"
    print(f"referat {command}: no project {pid} (known: {known})", file=sys.stderr)
    return 1


def run_tag(config: Config, meeting_id: str, project_ids: list[str]) -> int:
    """`referat tag <meeting-id> <project-id>...`. Idempotent, and several at a time.

    Unknown ids are **refused**, unlike in `untag`: an id no project answers to is
    an orphan, and the one way to create one on purpose should be deleting a
    project rather than mistyping at a prompt.
    """
    db = projects.ProjectsDB.load(config)
    if db.unreadable:
        return _unreadable_projects(db, "tag", writing=False)
    unknown = [pid for pid in project_ids if pid not in db.projects]
    if unknown:
        known = ", ".join(sorted(db.projects)) or "none yet"
        print(
            f"referat tag: no project {', '.join(unknown)} (known: {known})",
            file=sys.stderr,
        )
        return 1

    meeting = _resolve_meeting(config, meeting_id, "tag")
    if meeting is None:
        return 1

    added = projects.add_tags(meeting, project_ids)
    if added:
        meeting.save()
    print(_tag_report(meeting, added, project_ids, "tagged", "already tagged"))
    return 0


def run_untag(config: Config, meeting_id: str, project_ids: list[str]) -> int:
    """`referat untag <meeting-id> <project-id>...`. Idempotent, and orphans included."""
    meeting = _resolve_meeting(config, meeting_id, "untag")
    if meeting is None:
        return 1

    removed = projects.remove_tags(meeting, project_ids)
    if removed:
        meeting.save()
    print(_tag_report(meeting, removed, project_ids, "untagged", "was not tagged"))
    return 0


def _tag_report(
    meeting: Meeting, changed: list[str], asked: list[str], verb: str, unchanged: str
) -> str:
    """What `tag` and `untag` say afterwards, including when they did nothing.

    Idempotent commands that print nothing are indistinguishable from ones that
    failed silently, so both halves are named and the meeting's whole tag list is
    printed after.
    """
    lines = []
    if changed:
        lines.append(f"{meeting.id}: {verb} {', '.join(changed)}")
    skipped = [pid for pid in asked if pid not in changed]
    if skipped:
        lines.append(f"{meeting.id}: {', '.join(skipped)} {unchanged}")
    lines.append(f"tags: {', '.join(meeting.tags) if meeting.tags else 'none (untagged)'}")
    return "\n".join(lines)


def run_state(config: Config, meeting_id: str, transition: str) -> int:
    """`referat state <id> notes-written` — the one transition no pipeline can make.

    `/cleanup` is forbidden from touching `meta.json`, so it cannot record that it
    wrote `notes.md`; whoever spawned it says so here. The transition is accepted
    only **from `transcribed`**, and no other transition is accepted at all:
    `gate_failed` and `transcribed` are the pipeline's to write and `synced` is
    `project sync`'s, and a verb that let a caller claim any state would turn the
    lifecycle field from a record into a comment.
    """
    assert transition == NOTES_WRITTEN_VERB  # argparse `choices` allows nothing else
    meeting = _resolve_meeting(config, meeting_id, "state")
    if meeting is None:
        return 1

    if meeting.status is MeetingStatus.NOTES_WRITTEN:
        print(f"{meeting.id} is already {meeting.status}")
        return 0
    if meeting.status is not MeetingStatus.TRANSCRIBED:
        print(
            f"referat state: {meeting.id} is {meeting.status}, and "
            f"{NOTES_WRITTEN_VERB} is only reachable from "
            f"{MeetingStatus.TRANSCRIBED}",
            file=sys.stderr,
        )
        return 1

    meeting.status = MeetingStatus.NOTES_WRITTEN
    meeting.save()
    # The dashboard prints the status and takes its title from the H1 of the
    # notes that have just appeared, so both are stale until this runs.
    index.write_index(config)
    print(f"{meeting.id}: {meeting.status}")
    return 0


# --- referat promote --------------------------------------------------------


def run_promote(config: Config, meeting_id: str, *, release_audio: bool) -> int:
    """`referat promote <id> [--release-audio]` — the off-ramp out of staging.

    A meeting is recorded in the staging folder and moves into the meetings
    folder only once its WAVs are gone; a meeting whose transcript failed the
    quality gate keeps them and is therefore stuck there. It cannot be promoted
    *with* its audio, because "no WAV ever reaches the meetings folder" is the
    invariant the whole staging split exists for — deleting a file inside a
    synced folder does not delete it, so `audio_released` would become a lie
    about recordings of people who never asked to be recorded.

    So the off-ramp deletes the audio and then promotes, and `--release-audio`
    is what says so out loud. Without the flag this refuses and names the files,
    which is the entire safety of the bare form; the bare form itself is the
    retry for a promotion that failed to move the folder the first time.

    Accepting the transcript is what the flag means, so the meeting also stops
    being `gate_failed` and becomes `transcribed`: the transcript was never the
    thing in doubt — :attr:`referat.meeting.MeetingStatus.GATE_FAILED` is about
    the audio not having been trusted enough to delete, and this is somebody
    deciding otherwise.
    """
    meeting = _resolve_meeting(config, meeting_id, "promote")
    if meeting is None:
        return 1

    if meeting.dir.parent == config.paths.meetings_dir:
        print(f"{meeting.id} is already in {config.paths.meetings_dir}")
        return 0

    kept = [p for p in (meeting.mic_path, meeting.system_path) if p.exists()]
    if kept and not release_audio:
        names = ", ".join(p.name for p in kept)
        megabytes = sum(p.stat().st_size for p in kept) / 1e6
        print(
            f"referat promote: {meeting.id} still holds {names} ({megabytes:.1f} MB), "
            f"and audio may never enter {config.paths.meetings_dir}.\n"
            f"                 pass --release-audio to delete "
            f"{'them' if len(kept) != 1 else 'it'} and promote anyway - the "
            f"transcript is all that would be left - or `referat rerun "
            f"{meeting.id}` to try for a transcript the gate accepts",
            file=sys.stderr,
        )
        return 1

    if kept:
        # Imported inside the branch, as `rerun` imports its own module: this is
        # the one place a light verb reaches into the transcription module, and
        # it reaches for the two functions that own the rule rather than for a
        # copy of them. `transcribe`'s own imports are numpy and siblings, so
        # nothing here pulls in torch.
        from referat.transcribe import release_audio as delete_audio

        freed = delete_audio(meeting)
        if freed < 0:
            print(
                f"referat promote: could not delete {meeting.id}'s audio; "
                f"it stays in {meeting.dir.parent}",
                file=sys.stderr,
            )
            return 1
        print(f"deleted {', '.join(p.name for p in kept)} ({freed / 1e6:.1f} MB)")

    if meeting.status is MeetingStatus.GATE_FAILED:
        meeting.status = MeetingStatus.TRANSCRIBED
        meeting.save()

    from referat.transcribe import promote_meeting

    if not promote_meeting(meeting, config):
        # `promote_meeting` never raises and logs why; a meeting that could not
        # be moved is still a finished meeting with a transcript in it.
        print(
            f"referat promote: could not move {meeting.id} into "
            f"{config.paths.meetings_dir}; see the log",
            file=sys.stderr,
        )
        return 1

    index.write_index(config)
    print(f"{meeting.id}: {meeting.status}, moved into {config.paths.meetings_dir}")
    return 0


# --- referat reflow ---------------------------------------------------------


def run_reflow(config: Config, meeting_id: str | None) -> int:
    """`referat reflow [<meeting-id>]` - make an older transcript render as entries.

    A migration that stayed, because it is also the repair for a transcript
    somebody has mangled by hand. The rule it applies lives in
    :func:`referat.transcribe.reflow_transcript` beside the renderer that now
    writes the same shape, so the file Referat produces and the file it repairs
    cannot drift apart.

    **It rewrites no line.** Blank lines go *between* entries; every word of
    speech, every timestamp and every label is left exactly where it was. That is
    what keeps this on the right side of the immutability rule, and it is checked
    rather than asserted - the two line-anchored patterns in
    :mod:`referat.label` still match a reflowed transcript, because a blank line
    matches neither and is skipped.

    Idempotent, and it writes nothing when there is nothing to insert: a second
    run touches no file, which is what makes it safe to point at everything.
    """
    from referat.transcribe import ENTRY_RE, reflow_transcript

    if meeting_id:
        meeting = _resolve_meeting(config, meeting_id, "reflow")
        if meeting is None:
            return 1
        meetings = [meeting]
    else:
        meetings = load_meetings(config)

    if not meetings:
        print(f"No meetings in {config.paths.meetings_dir} yet.")
        return 0

    rewritten = 0
    for meeting in meetings:
        path = meeting.transcript_path
        try:
            before = path.read_text(encoding="utf-8")
        except OSError:
            print(f"{meeting.id}: no transcript.md")
            continue

        after, inserted = reflow_transcript(before)
        # Said out loud rather than silently skipped: a transcript this does not
        # fully recognise is one somebody should look at, and the lines it did
        # not recognise are exactly the ones it deliberately left alone.
        strange = sum(
            1
            for line in before.split("\n")
            if line.strip() and not line.startswith("## ") and not ENTRY_RE.match(line)
        )
        note = f" ({strange} unrecognised line(s) left alone)" if strange else ""

        if not inserted:
            print(f"{meeting.id}: already one entry per block{note}")
            continue
        try:
            paths.write_text_atomic(path, after)
        except OSError as exc:
            print(f"referat reflow: could not write {path}: {exc}", file=sys.stderr)
            return 1
        rewritten += 1
        print(f"{meeting.id}: {inserted} blank line(s) inserted{note}")

    if len(meetings) > 1:
        print(
            f"\n{len(meetings)} meeting(s), {rewritten} rewritten "
            f"(no speech, timestamp or label was changed)"
        )
    return 0


# --- referat relabel --------------------------------------------------------


def _relabel_complaint(config: Config, meeting: Meeting, owner: str) -> str:
    """Why this meeting's `ME` lines must be left alone, or `""` to rewrite them.

    The pipeline used to render a voiceprint-matched owner as `ME` while
    `referat label` spelled the same person's name out, so a transcript could
    carry one person under two labels. The renderer no longer does that, but the
    transcripts written while it did are still on disk with their audio released,
    and no `rerun` can regenerate them.

    Rewriting every `ME` would be the wrong repair, because `ME` also means *the
    microphone recorded this and nothing attributed it* - which on the mic channel
    is frequently somebody else in the room. The condition here is the one that
    can actually be checked: a meeting whose **microphone clustered into nothing
    but the owner** held one person on that channel, so every mic line is theirs
    however it came to be labeled. It is the same shape of test as
    :func:`referat.voices.bootstrap_owner`'s - measured off the clustering rather
    than inferred from the kind of meeting it was.
    """
    mic = voices.channel_speakers(meeting, meeting.mic_path.stem)
    if not mic:
        return "the microphone was never diarized, so its ME lines name nobody"
    resolved = {meeting.speaker_names.get(label, "") for label in mic}
    if "" in resolved:
        unnamed = sorted(label for label in mic if not meeting.speaker_names.get(label))
        return f"the microphone still has unnamed speakers ({', '.join(unnamed)})"
    if resolved != {owner}:
        others = ", ".join(sorted(name for name in resolved if name != owner))
        return f"the microphone also holds {others}, so its ME lines are ambiguous"
    return ""


def run_relabel(config: Config, meeting_id: str | None) -> int:
    """`referat relabel [<meeting-id>]` - spell the owner's name into older transcripts.

    A repair, like `referat reflow`, and for a defect of the same era: the
    pipeline wrote `ME:` for a cluster it had matched to the owner while
    :func:`referat.label.apply_name` wrote the name, so `2026-09-01_2102` came out
    with 178 `ME:` lines and 4 `Niklas:` lines for one voice. New meetings are
    right without this - :data:`referat.transcribe.ME_LABEL` is now only ever the
    fallback - but the files already written are not.

    **It rewrites no line**, in the narrow sense the immutability rule means:
    :func:`referat.label.relabel_transcript` is anchored on the timestamp and the
    colon that delimit the label field, so the word "me" inside somebody's speech
    is untouched. Idempotent, and it writes nothing when there is nothing to
    change.

    Refuses per meeting rather than globally, and says why each time - see
    :func:`_relabel_complaint` for the test and why it is that one.
    """
    from referat.label import relabel_transcript
    from referat.transcribe import ME_LABEL

    owner = config.speakers.owner_name.strip()
    if not owner:
        print(
            "referat relabel: [speakers].owner_name is empty, so there is no name "
            "to spell out. ME is already the only label the owner has.",
            file=sys.stderr,
        )
        return 1

    if meeting_id:
        meeting = _resolve_meeting(config, meeting_id, "relabel")
        if meeting is None:
            return 1
        meetings = [meeting]
    else:
        meetings = load_meetings(config)

    if not meetings:
        print(f"No meetings in {config.paths.meetings_dir} yet.")
        return 0

    rewritten = 0
    for meeting in meetings:
        if not meeting.transcript_path.exists():
            print(f"{meeting.id}: no transcript.md")
            continue
        why = _relabel_complaint(config, meeting, owner)
        if why:
            print(f"{meeting.id}: left alone - {why}")
            continue
        changed = relabel_transcript(meeting.transcript_path, {ME_LABEL: owner})
        if not changed:
            print(f"{meeting.id}: already spells {owner} out")
            continue
        rewritten += 1
        print(f"{meeting.id}: {changed} line(s) relabeled {ME_LABEL} -> {owner}")

    if rewritten:
        index.write_index(config)
    if len(meetings) > 1:
        print(
            f"\n{len(meetings)} meeting(s), {rewritten} rewritten "
            f"(no speech and no timestamp was changed)"
        )
    return 0


# --- referat delete ---------------------------------------------------------


def _folder_bytes(folder: Path) -> int:
    """How much disk a meeting folder holds, snippets and WAVs included."""
    total = 0
    for item in folder.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:  # pragma: no cover - a file that vanished mid-walk
            continue
    return total


def run_delete(config: Config, meeting_id: str, *, assume_yes: bool) -> int:
    """`referat delete <id> [--yes]` - remove a meeting and everything in it.

    The one deletion Referat did not have. `promote --release-audio` deletes the
    WAVs, `project rm` deletes a project and `label --forget` deletes a person;
    a recording that should never have been kept had to be deleted in Explorer,
    which also left the meetings `INDEX.md` describing it.

    Two things the confirmation has to say, because both are true and neither is
    obvious:

    * A meeting inside a **synced** meetings folder is not really gone. Dropbox
      keeps deleted files and prior versions on its own servers for weeks - the
      same fact that put recording and transcription in a staging folder in the
      first place. A staged meeting genuinely is gone.
    * The **voiceprints** this meeting contributed stay in `.voices/`, stamped
      with its id. Deleting a meeting is not deleting a person, and
      `referat label --forget <name>` is what is.

    Refuses while the meeting is live. `--yes` is not a convenience: the prompt
    reads `input()` and answers *no* on EOF, so a caller with no terminal - the
    extension - cannot confirm without it. See the `--forget` flag it copies.
    """
    meeting = _resolve_meeting(config, meeting_id, "delete")
    if meeting is None:
        return 1

    if meeting.status in (MeetingStatus.RECORDING, MeetingStatus.TRANSCRIBING):
        print(
            f"referat delete: {meeting.id} is {meeting.status} right now. "
            f"Deleting it underneath the tray would lose audio mid-write; "
            f"wait for it to finish.",
            file=sys.stderr,
        )
        return 1

    staged = meeting.dir.parent != config.paths.meetings_dir
    kept = [p for p in (meeting.mic_path, meeting.system_path) if p.exists()]
    megabytes = _folder_bytes(meeting.dir) / 1e6

    print(f"{meeting.id} ({meeting.status}, {format_duration(meeting.duration_seconds)})")
    print(f"  {meeting.dir}  -  {megabytes:.1f} MB")
    if kept:
        print(f"  audio still on disk: {', '.join(p.name for p in kept)}")
    if staged:
        print("  in staging, outside any synced folder, so this deletion is real")
    else:
        # The same argument `config.staging_dir` and `promote` already make.
        print(
            "  in the meetings folder. If that folder is synced, deleting a file\n"
            "  does not delete it: Dropbox keeps deleted files and prior versions\n"
            "  on its servers for weeks."
        )
    print(
        "  voiceprints this meeting contributed stay in the known-voices\n"
        "  database - `referat label --forget <name>` is how a person is removed"
    )

    if not assume_yes:
        try:
            answer = input(f"Delete {meeting.id}? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            print("Nothing was deleted.")
            return 0

    if not paths.remove_meeting_dir(meeting.dir):
        print(
            f"referat delete: could not remove {meeting.dir}; see the log",
            file=sys.stderr,
        )
        return 1

    index.write_index(config)
    print(f"deleted {meeting.id} ({megabytes:.1f} MB)")
    return 0


# --- referat status ---------------------------------------------------------


def status_document() -> dict[str, Any]:
    """`referat status --json`: what the tray is doing, as the extension reads it.

    The fourth JSON document, after `list`, `label` and `project list`, and added
    for the same reason as the first three: the status bar renders what Python
    already worked out rather than parsing prose written for a person. In
    particular `elapsed` is :func:`referat.meeting.format_duration`'s output, so
    the extension never grows a second duration formatter — the mistake this
    project has been pulled back from twice.

    `stale` is the interesting field. A status file whose pid is gone is the
    trace a crashed or killed tray leaves behind, and it is reported rather than
    hidden: `running` false with `stale` true is a different thing from no tray
    having run at all, and it is worth being able to say which.
    """
    current = status.read_status()
    if current is None:
        return {"running": False, "stale": False}
    if not status.is_running(current.pid):
        return {
            "running": False,
            "stale": True,
            "state": str(current.state),
            "meeting_id": current.meeting_id,
            "pid": current.pid,
            "updated_at": current.updated_at,
        }
    return {
        "running": True,
        "stale": False,
        "state": str(current.state),
        "meeting_id": current.meeting_id,
        "elapsed": _elapsed_short(current.started_at),
        "jobs": current.jobs,
        "pid": current.pid,
        "updated_at": current.updated_at,
    }


def run_status(as_json: bool = False) -> int:
    """`referat status`. Exit 0 when a tray is running, 1 when none is.

    A status file whose pid is dead is reported rather than hidden: it is the
    trace a crashed or killed tray leaves behind, and knowing it died while
    transcribing is worth more than a bare "not running".
    """
    if as_json:
        document = status_document()
        print(json.dumps(document, indent=2))
        return 0 if document["running"] else 1

    current = status.read_status()
    if current is None:
        print("Referat is not running.")
        return 1
    if not status.is_running(current.pid):
        print(
            f"Referat is not running.\n"
            f"stale status file from {current.updated_at} "
            f"(state: {current.state}, pid {current.pid})"
        )
        return 1

    line = str(current.state)
    if current.meeting_id:
        line += f"  {current.meeting_id}"
        elapsed = _elapsed(current.started_at)
        if elapsed:
            line += f"  {elapsed}"
    print(line)
    jobs = f", {current.jobs} transcription job{'s' if current.jobs != 1 else ''}"
    print(f"tray pid {current.pid}{jobs if current.jobs else ''}")
    print(f"updated {current.updated_at}")
    return 0


def _elapsed_short(started_at: str | None) -> str | None:
    """How long the current meeting has been going, as `M:SS`, or None.

    None rather than "" because this is a JSON field as well as half a sentence,
    and an absent duration is a different thing from a zero-length one.
    """
    if not started_at:
        return None
    try:
        started = dt.datetime.fromisoformat(started_at)
    except ValueError:
        return None
    return format_duration((dt.datetime.now() - started).total_seconds())


def _elapsed(started_at: str | None) -> str:
    """The same thing as a phrase, for the human-readable line."""
    elapsed = _elapsed_short(started_at)
    return f"{elapsed} so far" if elapsed else ""


# --- referat devices --------------------------------------------------------


DEVICE_HEADERS = ("INDEX", "NAME", "HOSTAPI", "CH", "RATE", "")
LOOPBACK_HEADERS = ("NAME", "CH", "RATE", "")


def _marker(selected: bool, default: bool) -> str:
    """The right-hand column: what the config picked, what Windows would pick."""
    parts = []
    if selected:
        parts.append("<- config")
    if default:
        parts.append("(default)")
    return " ".join(parts)


def _setting(key: str, value: str) -> str:
    """`[audio].mic_device = "Jabra"`, saying so when it is empty."""
    suffix = "" if value else "  (system default)"
    return f'[audio].{key} = "{value}"{suffix}'


def _input_section(sd: ModuleType, recorder: ModuleType, wanted: str) -> str:
    """The input devices, with the one `mic_device` resolves to marked."""
    lines = [_setting("mic_device", wanted)]

    # The recorder's own resolver, not a copy of its rules: a listing that
    # disagreed with what actually gets recorded would be worse than none.
    selected = recorder.resolve_mic_device(wanted)
    try:
        devices = list(sd.query_devices())
        hostapis = list(sd.query_hostapis())
    except Exception as exc:
        return "\n".join([*lines, f"could not enumerate input devices: {exc}"])

    try:
        default = sd.default.device[0]
    except Exception:
        default = -1

    rows: list[tuple[str, ...]] = []
    for index, device in enumerate(devices):
        if not device["max_input_channels"]:
            continue
        rows.append(
            (
                str(index),
                str(device["name"]),
                str(hostapis[device["hostapi"]]["name"]),
                str(device["max_input_channels"]),
                str(int(device["default_samplerate"])),
                _marker(index == selected, index == default),
            )
        )

    if wanted.strip() and selected is None:
        lines.append(f"nothing matches {wanted!r} - the default input will be recorded")
    if not rows:
        lines.append("no input devices")
        return "\n".join(lines)
    lines.append("")
    lines.append(render_table(rows, DEVICE_HEADERS, (0, 3, 4)))
    return "\n".join(lines)


def _loopback_section(recorder: ModuleType, wanted: str) -> str:
    """The WASAPI loopback sources, with the one `loopback_device` resolves to marked.

    Deliberately without an index column: PyAudio numbers its devices in its own
    namespace, and printing that next to sounddevice's would invite setting one
    from the other.
    """
    import pyaudiowpatch as pyaudio

    lines = [_setting("loopback_device", wanted)]
    audio = pyaudio.PyAudio()
    try:
        try:
            selected = recorder.resolve_loopback_device(audio, wanted)
        except recorder.RecorderError as exc:
            return "\n".join([*lines, f"no loopback available: {exc}"])
        # `_matches` rather than a substring test written out again here: the
        # question is whether the *recorder* considers this a match. Unlike the
        # mic resolver, this one has no way of saying "I fell back" — it returns
        # the default output's loopback either way — so ask afterwards.
        matched = bool(wanted.strip()) and recorder._matches(str(selected["name"]), wanted)
        if wanted.strip() and not matched:
            lines.append(f"nothing matches {wanted!r} - the default output will be recorded")

        rows: list[tuple[str, ...]] = []
        for info in audio.get_loopback_device_info_generator():
            resolved = info["index"] == selected["index"]
            rows.append(
                (
                    str(info["name"]),
                    str(info["maxInputChannels"]),
                    str(int(info["defaultSampleRate"])),
                    _marker(resolved and matched, resolved and not matched),
                )
            )
    finally:
        audio.terminate()

    if not rows:
        lines.append("no loopback devices")
        return "\n".join(lines)
    lines.append("")
    lines.append(render_table(rows, LOOPBACK_HEADERS, (1, 2)))
    return "\n".join(lines)


def run_devices(config: Config) -> int:
    """`referat devices`. What is plugged in, and what `[audio]` does with it."""
    import sounddevice as sd

    from referat import recorder

    print(_input_section(sd, recorder, config.audio.mic_device))
    print()
    print(_loopback_section(recorder, config.audio.loopback_device))
    return 0


# --- referat label ----------------------------------------------------------


def _label_complaint(args: argparse.Namespace) -> str | None:
    """What is wrong with this combination of `label` flags, or None.

    `label` has grown three non-interactive modes beside the prompt, and they do
    not compose: naming one speaker, dumping the meeting as JSON, and deleting a
    person are three different operations that happen to share a subcommand.
    Saying so in a sentence beats argparse naming the flags and leaving the
    caller to work out which pair it objected to.
    """
    modes = sum(bool(x) for x in (args.forget, args.speaker, args.as_json))
    if modes > 1:
        return "--forget, --speaker and --json are three different operations; pick one"
    if args.speaker and not args.name:
        return "--speaker needs --name"
    if args.name and not args.speaker:
        return "--name is only meaningful with --speaker"
    if (args.speaker or args.as_json) and not args.meeting_id:
        flag = "--speaker" if args.speaker else "--json"
        return f"{flag} needs a meeting id"
    if args.forget and args.meeting_id:
        # Forgetting is global by definition: it reverts the labels in *every*
        # transcript, so a meeting id here means the caller expects it not to.
        return "--forget acts on every meeting; it takes no meeting id"
    if args.yes and not args.forget:
        return "--yes only answers --forget's confirmation"
    return None


# --- Entry point ------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    if args.command == "status":
        return run_status(as_json=args.as_json)

    try:
        config = load_config() if args.command in NEEDS_CONFIG else None
    except ConfigError as exc:
        print(f"referat: {exc}", file=sys.stderr)
        return 1
    assert config is not None

    if args.command == "config":
        print(f"source: {config.source}")
        for section, values in asdict(config).items():
            if isinstance(values, dict):
                print(f"[{section}]")
                for key, value in values.items():
                    print(f"  {key} = {value}")
        return 0

    if args.command == "list":
        return run_list(config, as_json=args.as_json)

    if args.command == "index":
        return index.run(config)

    if args.command == "project":
        return run_project(config, args)

    if args.command == "tag":
        return run_tag(config, args.meeting_id, args.project_id)

    if args.command == "untag":
        return run_untag(config, args.meeting_id, args.project_id)

    if args.command == "state":
        return run_state(config, args.meeting_id, args.transition)

    if args.command == "promote":
        return run_promote(config, args.meeting_id, release_audio=args.release_audio)

    if args.command == "reflow":
        return run_reflow(config, args.meeting_id)

    if args.command == "relabel":
        return run_relabel(config, args.meeting_id)

    if args.command == "delete":
        return run_delete(config, args.meeting_id, assume_yes=args.assume_yes)

    if args.command == "devices":
        return run_devices(config)

    if args.command == "label":
        # Imported here rather than at module scope so `referat --version` and
        # `referat config` stay instant, and so a broken sound device cannot
        # cost the rest of the CLI.
        from referat import label as labelling

        complaint = _label_complaint(args)
        if complaint:
            # Checked here rather than through argparse's mutually-exclusive
            # groups, whose generated messages name flags without saying what
            # the combination would have meant.
            print(f"referat label: {complaint}", file=sys.stderr)
            return 2
        if args.forget:
            return labelling.run_forget(config, args.forget, assume_yes=args.yes)
        if args.speaker:
            assert args.meeting_id and args.name
            return labelling.run_apply(config, args.meeting_id, args.speaker, args.name)
        if args.as_json:
            assert args.meeting_id
            return labelling.run_json(config, args.meeting_id)
        return labelling.run(config, args.meeting_id)

    if args.command == "rerun":
        # And this one keeps the `transcribe` extra out of every other command.
        from referat import rerun as rerunning

        return rerunning.run(config, args.meeting_id, force=args.force)

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
