"""The `referat` command line interface.

`config` since build step 1, `label` since 7b, `list`, `rerun` and `status`
since step 8, `devices` since the first USB microphone, `index` with the
meetings folder scaffold at step 10, `hotwords` at step 12b, `project`, `tag`,
`untag` and `state` at step 14, and `promote` at step 15. Between them they are
the whole of Referat that is not the tray: what has been recorded, what still
needs a name, what work each meeting belongs to, what the tray is doing, what it
will record with, what Whisper is told about before it transcribes, how to
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
without three gigabytes of torch. `project`, `tag`, `untag`, `state` and `hotwords` are a
JSON read and a JSON write and need no extra at all — only step 13's `project
link-doc` and `project sync` will need `digest`. `label` and `devices` are
imported here rather than at module scope for the same kind of reason: a broken
sound device should not be able to cost the rest of the CLI.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import signal
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from referat import __version__, build_info, index, paths, projects, status, voices
from referat.config import Config, ConfigError, load_config
from referat.meeting import (
    Meeting,
    MeetingStatus,
    format_duration,
    load_meetings,
    resolve_meeting,
)

log = logging.getLogger(__name__)

NEEDS_CONFIG = (
    "config",
    "debleed",
    "delete",
    "devices",
    "hotwords",
    "index",
    "label",
    "list",
    "project",
    "promote",
    "reflow",
    "relabel",
    "rerun",
    "show",
    "state",
    "tag",
    "transcript",
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

    show = subcommands.add_parser(
        "show",
        help="one meeting's whole record: lifecycle, models, speakers, scores",
        description=(
            "Everything meta.json holds about one meeting, plus the handful of "
            "answers every other surface asks Python for rather than deriving: "
            "the title, the formatted duration, whether the audio is still on "
            "disk and whether the folder is still in staging. The per-channel "
            "`speakers` block carries each cluster's match score and its "
            "runner-up, recorded even where the match was refused, which is the "
            "only material there is for calibrating [speakers]' thresholds."
        ),
    )
    show.add_argument("meeting_id", help="e.g. 2026-08-27_1400")
    show.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="emit the whole record as JSON, for the command center",
    )

    transcript = subcommands.add_parser(
        "transcript",
        help="a meeting's transcript, parsed: who spoke, how often, for how long",
        description=(
            "Read transcript.md back through the parser that lives beside the "
            "renderer that wrote it. The table is one row per speaker label with "
            "how many entries and how many words carry it, which is the one "
            "question a rendered transcript answers badly. --json is the entries "
            "themselves, timestamps in seconds as well as rendered, for the "
            "command center's transcript pane."
        ),
    )
    transcript.add_argument("meeting_id", help="e.g. 2026-08-27_1400")
    transcript.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="emit the parsed entries as JSON, for the command center",
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
        "hotwords",
        help="the words Whisper is told about before it transcribes",
        description=(
            "Print the one global hotword list, merged from three sources: "
            "[transcription].hotword_extras, every name in the known-voices "
            "database, and every project's glossary in projects.json. It is one "
            "list for the machine and never one per project, because a meeting is "
            "tagged after it has been transcribed. Whisper's prompt window leaves "
            "223 tokens for it, so the list is capped in that priority order - "
            "extras, then names, then glossaries - and this says what the cap "
            "dropped. Capped here rather than by faster-whisper, which slices an "
            "over-long list mid-name and says nothing."
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

    debleed = subcommands.add_parser(
        "debleed",
        help="collapse lines a transcript recorded twice, once per channel",
        description=(
            "When a hybrid meeting's remote audio comes out of a speaker in the "
            "same room as the microphone, the far end is recorded twice and lands "
            "in the transcript twice. New meetings have this suppressed during "
            "transcription; this repairs the ones already written, whose audio is "
            "gone and which no rerun can regenerate. It only touches a meeting "
            "where a name resolved to clusters on both channels, it keeps "
            "whichever copy contains the other, and it never touches a line short "
            "enough to be a genuine second 'Yeah'. Dry by default: --apply writes, "
            "and records every removed line in meta.json first, so the removal can "
            "be read back and undone by hand."
        ),
    )
    debleed.add_argument(
        "meeting_id",
        nargs="?",
        help="a single meeting, e.g. 2026-08-27_1400. Omit for all of them",
    )
    debleed.add_argument(
        "--apply",
        action="store_true",
        help="actually remove the lines. Without it, nothing is written",
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
        "--drop-voiceprint",
        metavar="SPEAKER_NN",
        dest="drop_voiceprint",
        help="remove the voiceprint this meeting's cluster contributed, leaving the "
        "person and their transcript labels alone. For a cluster that turned out "
        "to be a loudspeaker rather than the person it was correctly named after",
    )
    label.add_argument(
        "--yes",
        action="store_true",
        help="skip the confirmation --forget and --drop-voiceprint ask for",
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
    """One line of `referat list`, as its six columns.

    A meeting that lost a channel says so in the STATUS cell. It is the only
    thing in this table that is not a lifecycle value, and it earns the place:
    `2026-09-02_1001` was twenty-four minutes with no microphone in it and this
    row read `transcribed`, which is true of the transcription and worthless as
    a description of the meeting.
    """
    unnamed = len(voices.unknown_speakers(meeting))
    lost = f" no {'/'.join(meeting.missing_channels)}" if meeting.missing_channels else ""
    return (
        meeting.id,
        format_duration(meeting.duration_seconds),
        str(meeting.status) + lost,
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
                "missing_channels": list(m.missing_channels),
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
    # Still in staging means the audio is still there, and there are two reasons
    # for that which want opposite advice. A meeting the gate refused wants a
    # `rerun`; one kept on request has a fine transcript already and wants
    # `promote --release-audio` when its audio has served its purpose. The
    # `audio_kept` note in `meta.json` is what tells them apart — before
    # `[transcription].keep_audio` existed there was only the first kind, and this
    # line said `run: referat rerun` for both.
    # A meeting still being recorded or transcribed is in staging because it has
    # not finished, which is neither of those reasons and wants no advice at all —
    # `referat rerun` on a live recording is the worst thing this line could
    # suggest, and it suggested it.
    staged = [
        m
        for m in meetings
        if m.dir.parent != config.paths.meetings_dir
        and m.status not in (MeetingStatus.RECORDING, MeetingStatus.TRANSCRIBING)
    ]
    untrusted = [m for m in staged if not m.transcription.get("audio_kept")]
    on_request = [m for m in staged if m.transcription.get("audio_kept")]
    if untrusted:
        summary += f", {len(untrusted)} still local with audio kept - run: referat rerun"
    if on_request:
        summary += (
            f", {len(on_request)} keeping audio on request "
            f"- run: referat promote --release-audio"
        )
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


# --- referat show, referat transcript ---------------------------------------


def show_document(config: Config, meeting: Meeting) -> dict[str, Any]:
    """`referat show <id> --json`: one meeting's whole record.

    The fifth JSON document, and the first written for the command center rather
    than for the extension. It is `meta.json` itself under `meta` — through
    :meth:`referat.meeting.Meeting.to_json`, so the pipeline's own serializer
    decides what a record is — wrapped in the derived answers every other surface
    already asks Python for instead of working out: the title, the formatted
    duration, the audio state and whether the folder is still staged.

    Those four are the whole reason this is not `cat meta.json`. Each of them
    exists exactly once in Python and is shared by three or more callers *so that
    they cannot disagree*, which is the argument :func:`list_document` makes at
    greater length.

    The interesting half is inside `meta`, and it is `transcription`: the model
    and device a run used, the per-channel quality numbers the gate judged, and
    the per-channel `speakers` block with each cluster's match score and its
    runner-up. That last is recorded even where the match was *refused*, and
    nothing has ever printed it — which makes this the first way to read the
    near-misses that are the only material for calibrating
    `[speakers].match_threshold` and `match_margin` against real voices.
    """
    return {
        "id": meeting.id,
        "dir": str(meeting.dir),
        "staged": meeting.dir.parent != config.paths.meetings_dir,
        "title": index.meeting_title(meeting),
        "duration": format_duration(meeting.duration_seconds),
        "audio": audio_state(meeting),
        "unnamed": voices.unknown_speakers(meeting),
        "projects": projects.ProjectsDB.load(config).name_map(),
        "files": {
            "transcript": meeting.transcript_path.exists(),
            "notes": (meeting.dir / paths.NOTES_MD).exists(),
            "mic": meeting.mic_path.exists(),
            "system": meeting.system_path.exists(),
        },
        "meta": meeting.to_json(),
    }


def run_show(config: Config, meeting_id: str, as_json: bool = False) -> int:
    """`referat show`. The record for a person, or the document for the window."""
    meeting = _resolve_meeting(config, meeting_id, "show")
    if meeting is None:
        return 1
    document = show_document(config, meeting)
    if as_json:
        print(json.dumps(document, indent=2))
        return 0

    known = document["projects"]
    lost = f" (no {'/'.join(meeting.missing_channels)})" if meeting.missing_channels else ""
    paused = f", {len(meeting.pauses)} pause(s)" if meeting.pauses else ""
    lines = [
        meeting.id if document["title"] == meeting.id else f"{meeting.id}  {document['title']}",
        f"  status     {meeting.status}{lost}",
        f"  started    {meeting.started_at.isoformat(timespec='seconds')}",
        f"  duration   {document['duration']}{paused}",
        f"  audio      {document['audio']}",
        f"  folder     {meeting.dir}" + ("  (staging)" if document["staged"] else ""),
        f"  tags       {tags_cell(meeting, known)}",
    ]
    transcription = meeting.transcription
    if transcription:
        model = transcription.get("model") or "?"
        device = transcription.get("device") or "?"
        lines.append(f"  model      {model} on {device}")
    for channel, block in sorted((transcription.get("channels") or {}).items()):
        if isinstance(block, dict):
            lines.append(f"  {channel:<10} {_channel_line(block)}")
    for label, speaker in sorted(_speaker_blocks(transcription).items()):
        lines.append(f"  {label:<10} {_speaker_line(meeting, label, speaker)}")
    if document["unnamed"]:
        lines.append(
            f"  unnamed    {', '.join(document['unnamed'])} "
            f"- run: referat label {meeting.id}"
        )
    print("\n".join(lines))
    return 0


CHANNEL_NUMBERS = ("avg_logprob", "compression_ratio", "no_speech_prob", "voiced_seconds")
"""The quality numbers `referat show` prints, in the order the gate reads them.

Not every key of the block: `peak`, `speech_seconds` and `language` are in the
JSON for whoever needs them, and these four are the ones a person calibrating
`[transcription]`'s thresholds is looking at. `voiced_seconds` is here because it
is what decides a channel is *silent* rather than garbled, which is the verdict
most likely to surprise — an in-person meeting's loopback is silent and clean."""


def _channel_line(block: dict[str, Any]) -> str:
    """One transcribed channel's quality numbers, as the gate saw them."""
    parts = [f"{block.get('segments', 0)} segments"]
    for key in CHANNEL_NUMBERS:
        value = block.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            parts.append(f"{key}={value:.3f}")
    if block.get("silent"):
        parts.append("silent")
    parts.append("clean" if block.get("clean") else "not clean")
    diarization = block.get("diarization")
    if isinstance(diarization, dict):
        turns = diarization.get("turns")
        state = diarization.get("status") or "?"
        parts.append(f"diarization {state}" + (f", {turns} turns" if turns else ""))
    return ", ".join(parts)


def _speaker_blocks(transcription: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Every `SPEAKER_NN` across both channels' `speakers` maps, flattened.

    The numbering runs across the meeting rather than the channel — see
    :func:`referat.diarize.assign` — so a label appears under exactly one channel
    and flattening the two maps cannot put two people behind one key.
    """
    out: dict[str, dict[str, Any]] = {}
    for block in (transcription.get("channels") or {}).values():
        if not isinstance(block, dict):
            continue
        for label, speaker in (block.get("speakers") or {}).items():
            if isinstance(speaker, dict):
                out[str(label)] = speaker
    return out


def _speaker_line(meeting: Meeting, label: str, speaker: dict[str, Any]) -> str:
    """What a diarized cluster resolved to, and what the match nearly said.

    The candidate and its runner-up are printed whenever they are recorded,
    including for a cluster the threshold-and-margin rule *refused*. Those are
    the near-misses, and they are the only material there is for judging whether
    the two numbers in `[speakers]` are set anywhere near right — the refused
    ones more than the accepted, since a refusal is where a threshold is felt.

    `speaker_names` wins over the per-channel `name` where the two differ,
    because it is the authority on who a label is and `referat label` writes it.
    The line then says what the match *would* have called the cluster, which on
    a hand-named speaker is exactly the comparison worth having.
    """
    name = meeting.speaker_names.get(label) or speaker.get("name")
    parts = [str(name) if name else "unnamed"]
    if speaker.get("echo"):
        parts.append("echo")
    match = speaker.get("match")
    if isinstance(match, dict):
        verdict = "accepted" if match.get("accepted") else "refused"
        parts.append(f"match {match.get('name') or '?'} {verdict}")
        for key, caption in (("score", "score"), ("runner_up", "runner-up")):
            value = match.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                parts.append(f"{caption} {value:.3f}")
    snippets = speaker.get("snippets")
    if isinstance(snippets, list) and snippets:
        parts.append(f"{len(snippets)} snippets")
    return "  ".join(parts)


def transcript_document(config: Config, meeting: Meeting) -> dict[str, Any]:
    """`referat transcript <id> --json`: the rendered transcript, parsed back.

    The sixth JSON document, for the command center's transcript pane. The window
    could read `transcript.md` itself — it is Markdown in a folder — and
    deliberately does not, for the reason it may not read `meta.json`: the shape
    of an entry is :mod:`referat.transcribe`'s to describe, and a second parser
    in the UI would be the first thing to disagree with the renderer the day that
    shape moves. :func:`referat.transcribe.parse_transcript` is the one parser,
    and it lives beside :func:`referat.transcribe.render_transcript`.

    Each entry carries its timestamp twice, as seconds and as the `HH:MM:SS` the
    file renders, because the two are used for different things: the number is
    what a cross-link from `notes.md` is matched against, and the string is what
    a reader sees — and formatting it in the UI would put
    :func:`referat.transcribe.format_timestamp`'s three lines in a second place.

    **The timestamp is audio-elapsed with pauses excluded, and it is not an
    offset into audio.** By the time anybody reads a transcript the WAVs are
    normally deleted, so a cross-link is a scroll position and never a seek.

    `labels` is the distinct speaker labels in order of first appearance, which
    is the order :func:`referat.diarize.assign` numbers them in.
    """
    from referat.transcribe import parse_transcript

    path = meeting.transcript_path
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        text = ""
    entries, unparsed = parse_transcript(text)
    labels: list[str] = []
    for _at, label, _text in entries:
        if label not in labels:
            labels.append(label)
    return {
        "id": meeting.id,
        "dir": str(meeting.dir),
        "title": index.meeting_title(meeting),
        "path": str(path),
        "exists": path.exists(),
        "duration_seconds": meeting.duration_seconds,
        "duration": format_duration(meeting.duration_seconds),
        "labels": labels,
        "unparsed": unparsed,
        "entries": [
            {"at": at, "time": _hms(at), "label": label, "text": text}
            for at, label, text in entries
        ],
    }


TRANSCRIPT_HEADERS = ("SPEAKER", "ENTRIES", "WORDS", "SHARE")
TRANSCRIPT_RIGHT_ALIGNED = (1, 2, 3)


def run_transcript(config: Config, meeting_id: str, as_json: bool = False) -> int:
    """`referat transcript`. Who spoke, how often, and how much of the talking.

    The table is deliberately not the transcript itself, which is a file you can
    already open and which this console cannot reliably print — the code page
    here is not UTF-8, and a transcript is the document in this project most
    likely to hold a character it cannot encode. What it prints instead is the
    question a rendered transcript answers worst: how the meeting divided up.

    `SHARE` is a share of words rather than of time. Time would read better and
    is not recoverable: the file records where an entry *started* and never how
    long it ran, so a share of seconds would have to invent an end for each one.
    """
    meeting = _resolve_meeting(config, meeting_id, "transcript")
    if meeting is None:
        return 1
    document = transcript_document(config, meeting)
    if as_json:
        print(json.dumps(document, indent=2))
        return 0

    if not document["exists"]:
        print(f"referat transcript: {meeting.id} has no transcript.md yet", file=sys.stderr)
        return 1
    entries = document["entries"]
    if not entries:
        print(f"{meeting.id}: transcript.md holds no entry this parser recognises.")
        return 1

    counts: dict[str, list[int]] = {}
    for entry in entries:
        row = counts.setdefault(entry["label"], [0, 0])
        row[0] += 1
        row[1] += len(entry["text"].split())
    total = sum(row[1] for row in counts.values()) or 1
    rows = [
        (label, str(said), str(words), f"{100 * words / total:.0f}%")
        for label, (said, words) in sorted(counts.items(), key=lambda item: -item[1][1])
    ]
    print(render_table(rows, TRANSCRIPT_HEADERS, TRANSCRIPT_RIGHT_ALIGNED))
    summary = f"\n{len(entries)} entries, {len(counts)} speakers, {document['duration']}"
    # Never zero — the `## Meeting ...` header is one of them — so this only says
    # something when there is more than the header it already expects.
    if document["unparsed"] > 1:
        summary += f", {document['unparsed'] - 1} lines this parser did not recognise"
    print(summary)
    return 0


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


def project_names(config: Config) -> tuple[dict[str, str], str]:
    """Every project id and what it is called, or the complaint that stopped it.

    The tag picker's whole reading vocabulary. It needs the map **fresh every
    time it opens** — the sidebar's picker took it from a cached listing and
    offered one of two projects, because nothing invalidated that cache — and it
    may not open `projects.json` itself, so this is the one call that answers
    both.

    Deliberately not :func:`project_document`, which answers a superset and pays
    a full `load_meetings` scan of both roots for the per-project meeting counts.
    A picker wants a map of names, and this is one file read.

    The complaint is `writing=False`'s, because that is what this is: with no
    names loaded every tag would render as an orphan, which is a worse thing to
    show than a refusal.
    """
    db = projects.ProjectsDB.load(config)
    if db.unreadable:
        return {}, _unreadable_complaint(db, writing=False)
    return db.name_map(), ""


def create_project(config: Config, name: str) -> tuple[projects.Project | None, str]:
    """Make a project, or say why not. The only implementation of creating one.

    Returns the :class:`referat.projects.Project` **object**, which is the whole
    point: an id is `slugify` plus a `-2` collision suffix, so no caller can work
    it out from the name, and looking it back out of `project list` by display
    name is wrong the moment two projects share one — which is allowed. The
    extension had exactly that bug before `project add --json` existed; in
    process there is no JSON to go through at all.

    The shape is :func:`referat.meeting.resolve_meeting`'s — a value and an
    unprefixed complaint, empty when it worked — rather than an
    :class:`Outcome`, because `None` and a failure flag would be the same fact
    twice.

    The unreadable guard comes before the name check, as it always has: a name
    complaint about a file that is about to be overwritten with an empty list is
    advice about the wrong problem.
    """
    db = projects.ProjectsDB.load(config)
    if db.unreadable:
        return None, _unreadable_complaint(db, writing=True)
    complaint = projects.name_complaint(name)
    if complaint:
        return None, f"a project name {complaint}"
    project = db.add(name)
    db.save()
    return project, ""


def run_project(config: Config, args: argparse.Namespace) -> int:
    """`referat project <verb>`. Every verb here is a JSON read and a JSON write."""
    if args.verb is None:
        print("referat project: pick a verb - add, rename, rm or list", file=sys.stderr)
        return 2

    if args.verb == "list":
        return run_project_list(config, as_json=args.as_json)

    if args.verb == "add":
        # Delegated before the shared load below, or `project add` would read
        # `projects.json` twice: once for the guard here and once inside
        # `create_project`, which owns the guard for its own callers anyway.
        project, complaint = create_project(config, args.name)
        if project is None:
            print(f"referat project add: {complaint}", file=sys.stderr)
            return 1
        if getattr(args, "as_json", False):
            print(json.dumps(project.to_json(), indent=2))
            return 0
        print(f"{project.id}  {project.name}")
        return 0

    db = projects.ProjectsDB.load(config)
    if db.unreadable:
        return _unreadable_projects(db, f"project {args.verb}", writing=True)

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


@dataclass(frozen=True)
class Outcome:
    """What a mutation did, in the words of whoever owns the rule.

    The in-process form of what `cli.ts`'s `mutate` gives the VS Code extension:
    a refusal reaches the user as the sentence the rule's owner wrote, never as
    something the caller paraphrased. The extension gets it as the CLI's stderr;
    the command center gets it as this.

    **`message` is unprefixed and the caller adds its own.** Not a style
    preference — the prefix is direction-dependent. `referat tag` says `referat
    tag:` and `referat untag` says `referat untag:`, while the command center's
    picker calls both directions in one gesture and has no command to name, so a
    prefix baked in here would be wrong for somebody. That is exactly the
    reasoning :func:`referat.meeting.resolve_meeting` already records for
    returning its complaint rather than printing it.

    Deliberately not named for writing: an idempotent no-op comes back `ok` with
    nothing written, and that is the case this report exists to describe.
    """

    ok: bool
    message: str


def _unreadable_complaint(db: projects.ProjectsDB, writing: bool) -> str:
    """Why a command stops when it cannot tell an empty projects file from a broken one.

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
    return (
        f"{db.path} exists but could not be read, {consequence}. "
        f"Fix or delete that file first."
    )


def _unreadable_projects(db: projects.ProjectsDB, command: str, writing: bool) -> int:
    print(f"referat {command}: {_unreadable_complaint(db, writing)}", file=sys.stderr)
    return 1


def _unknown_complaint(db: projects.ProjectsDB, ids: Sequence[str]) -> str:
    """Ids nothing resolves, and what would have worked. One id or many, one sentence."""
    known = ", ".join(sorted(db.projects)) or "none yet"
    return f"no project {', '.join(ids)} (known: {known})"


def _no_such_project(db: projects.ProjectsDB, pid: str, command: str) -> int:
    print(f"referat {command}: {_unknown_complaint(db, [pid])}", file=sys.stderr)
    return 1


def apply_tags(
    config: Config,
    meeting_id: str,
    *,
    add: Sequence[str] = (),
    remove: Sequence[str] = (),
) -> Outcome:
    """Add and remove project tags on one meeting. The only implementation of that.

    `referat tag` is this with `add` alone, `referat untag` is this with `remove`
    alone, and the command center's tag picker is this with both — which is why
    it takes both rather than being two functions. A picker is a diff, and doing
    it in one call means one `meta.json` write rather than two.

    Deliberately a **diff and never a replacement**. A `tags=[...]` form that
    worked out the difference in here is tempting, and it would make the
    two-sets bug in a picker structurally impossible — but it cannot express
    `referat tag`, which must not remove anything, and a replacement silently
    drops any tag the caller failed to render. A tag disappearing quietly off
    three meetings is the failure this codebase keeps designing against, so the
    diff form is the safe one.

    **The projects file is opened only when something is being added**, and that
    asymmetry is load-bearing rather than an optimization. `remove_tags` asks
    nothing about whether an id names a real project, because an orphan is
    precisely the tag somebody needs to be able to take off a meeting; guarding
    an untag on the file being readable would make a broken `projects.json` the
    one thing that pins an orphan to a meeting forever. So a pure removal never
    reads it, exactly as `referat untag` never has.

    Additions are applied before removals. Nothing here refuses an id in both
    lists — the picker cannot produce one, since its two sets are disjoint by
    construction — and if one ever arrived it would be added and then removed,
    which is what the arguments literally ask for.
    """
    add, remove = list(add), list(remove)
    if add:
        db = projects.ProjectsDB.load(config)
        if db.unreadable:
            return Outcome(False, _unreadable_complaint(db, writing=False))
        unknown = [pid for pid in add if pid not in db.projects]
        if unknown:
            # Refused, unlike a removal: an id no project answers to is an
            # orphan, and the one way to make one on purpose should be deleting a
            # project rather than mistyping at a prompt.
            return Outcome(False, _unknown_complaint(db, unknown))

    meeting, why = resolve_meeting(config, meeting_id)
    if meeting is None:
        return Outcome(False, why)

    added = projects.add_tags(meeting, add) if add else []
    removed = projects.remove_tags(meeting, remove) if remove else []
    if added or removed:
        meeting.save()
    lines = [
        *_tag_lines(meeting.id, added, add, "tagged", "already tagged"),
        *_tag_lines(meeting.id, removed, remove, "untagged", "was not tagged"),
        f"tags: {', '.join(meeting.tags) if meeting.tags else 'none (untagged)'}",
    ]
    return Outcome(True, "\n".join(lines))


def _tag_lines(
    meeting_id: str, changed: list[str], asked: list[str], verb: str, unchanged: str
) -> list[str]:
    """What one direction of a tag change says, as zero, one or two lines.

    Idempotent commands that print nothing are indistinguishable from ones that
    failed silently, so both halves are named: what changed, and what was asked
    for and did not.

    The meeting's whole tag list is *not* appended here, which is the one thing
    that stopped this being reusable. It has to be printed once, after **both**
    directions have been applied — printed per direction it would appear twice,
    the first time showing a list that was true for a moment in the middle of a
    write nobody asked to see.
    """
    lines = []
    if changed:
        lines.append(f"{meeting_id}: {verb} {', '.join(changed)}")
    skipped = [pid for pid in asked if pid not in changed]
    if skipped:
        lines.append(f"{meeting_id}: {', '.join(skipped)} {unchanged}")
    return lines


def run_tag(config: Config, meeting_id: str, project_ids: list[str]) -> int:
    """`referat tag <meeting-id> <project-id>...`. Idempotent, and several at a time."""
    outcome = apply_tags(config, meeting_id, add=project_ids)
    if not outcome.ok:
        print(f"referat tag: {outcome.message}", file=sys.stderr)
        return 1
    print(outcome.message)
    return 0


def run_untag(config: Config, meeting_id: str, project_ids: list[str]) -> int:
    """`referat untag <meeting-id> <project-id>...`. Idempotent, and orphans included."""
    outcome = apply_tags(config, meeting_id, remove=project_ids)
    if not outcome.ok:
        print(f"referat untag: {outcome.message}", file=sys.stderr)
        return 1
    print(outcome.message)
    return 0


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
        # A rerun is the advice for a meeting the gate refused, and no advice at
        # all for one kept on request: its gate already passed, so a rerun would
        # keep the audio all over again.
        retry = (
            ""
            if meeting.transcription.get("audio_kept")
            else f" - or `referat rerun {meeting.id}` to try for a transcript the gate accepts"
        )
        print(
            f"referat promote: {meeting.id} still holds {names} ({megabytes:.1f} MB), "
            f"and audio may never enter {config.paths.meetings_dir}.\n"
            f"                 pass --release-audio to delete "
            f"{'them' if len(kept) != 1 else 'it'} and promote anyway - the "
            f"transcript is all that would be left{retry}",
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


# --- referat debleed --------------------------------------------------------


def _debleed_complaint(meeting: Meeting) -> str:
    """Why this meeting cannot be de-duplicated from its rendered transcript, or `""`.

    The structural gate, and it is the whole reason this command is defensible on
    a file with no channel column. A rendered entry does not say which WAV it came
    out of, but `meta.json` still records which `SPEAKER_NN` clustered on which
    channel and what each resolved to — so a **name resolving to clusters on both
    channels** is a person who was recorded twice, and a name on the microphone
    alone is somebody in the room who cannot have been. Everything this command
    removes is a pair where at least one side carries a two-channel name.

    What the gate cannot do is say which of two `Benjamin:` lines is the
    microphone's. That is unrecoverable for a file whose audio is gone, and it is
    why the kept copy is chosen by containment rather than by provenance.
    """
    channels = meeting.transcription.get("channels") or {}
    if not isinstance(channels, dict) or not channels:
        return "no meta.json record of which channels held which speakers"
    if (meeting.transcription.get("bleed") or {}).get("status") == "suppressed":
        # Already cleaned where the evidence was strongest. Running the weaker
        # rule over the result would be double jeopardy on the survivors.
        return "the pipeline already suppressed this meeting's bleed"
    system = channels.get("system")
    if not isinstance(system, dict) or system.get("silent") or not system.get("segments"):
        return "the loopback channel held no voice, so nothing here was recorded twice"
    if not _two_channel_names(meeting):
        return "no name resolves to clusters on both channels, so nothing here is echo"
    return ""


def _two_channel_names(meeting: Meeting) -> set[str]:
    """Names whose clusters appear on more than one channel — the ones recorded twice."""
    where: dict[str, set[str]] = {}
    for channel, entry in (meeting.transcription.get("channels") or {}).items():
        if not isinstance(entry, dict):
            continue
        for label in entry.get("speakers") or {}:
            if name := meeting.speaker_names.get(label):
                where.setdefault(name, set()).add(str(channel))
    return {name for name, channels in where.items() if len(channels) > 1}


def run_debleed(config: Config, meeting_id: str | None, *, apply: bool = False) -> int:
    """`referat debleed [<meeting-id>] [--apply]` - collapse lines recorded twice.

    The repair for a transcript written before the pipeline suppressed bleed,
    whose audio has since been released so no `rerun` can regenerate it. Third in
    the family after `reflow` and `relabel`, and by some distance the most
    invasive: those two insert whitespace and rewrite a metadata field, and this
    one **deletes whole entries**.

    So it is dry by default and says what it would do. `--apply` writes, and
    writes `meta.json` *first*: every removed entry is recorded under
    `transcription.debleed.removed` with the line kept in its place, before the
    transcript is touched. That is what keeps this on the right side of the
    immutability rule, whose stated reason is that a transcript somebody has fixed
    is no longer evidence of what was said. A removal you can read back and undo
    by hand is still evidence; one you cannot is not. It is the same move
    `speaker_names` makes for `--forget` — keep the mapping that makes the
    operation reversible, because the Markdown alone cannot say what it used to
    be. An interruption between the two writes leaves a record of a removal that
    did not happen, which is noise; the other order would leave a removal with no
    record, which is the thing being avoided.

    **Which copy is kept is decided by containment and nothing else**: the one
    whose words include the other's, and on a tie the longer. Not the
    better-punctuated one, which is the obvious rule and was measured to be a coin
    flip - across 67 pairs the longer copy was better punctuated 24 times, worse
    20, tied 23. Containment is the only tie-break that cannot lose a word.

    Whole lines are deleted from the file and nothing is re-rendered, exactly as
    :func:`referat.transcribe.reflow_transcript` works: every surviving line stays
    byte-identical, so a hand-annotated transcript is not quietly reformatted and
    no timestamp is rounded back through `format_timestamp`.

    **Convergent rather than idempotent**, which is the one place this differs
    from `reflow` and `relabel`. Pairing is greedy and disjoint, so removing a
    line can leave two others adjacent that were not before: on
    `2026-09-02_1059` the passes went 66, then 1, then none. It reaches a fixed
    point and stops, and `transcription.debleed.removed` **accumulates across
    passes** rather than being replaced - without that, a second pass would leave
    67 lines gone from the file and 1 recorded, which is exactly the
    unreviewable deletion the record exists to prevent.
    """
    from referat.bleed import duplicates, tokens
    from referat.transcribe import parse_entry

    if meeting_id:
        meeting = _resolve_meeting(config, meeting_id, "debleed")
        if meeting is None:
            return 1
        meetings = [meeting]
    else:
        meetings = load_meetings(config)

    if not meetings:
        print(f"No meetings in {config.paths.meetings_dir} yet.")
        return 0

    settings = config.bleed
    rewritten = 0
    for meeting in meetings:
        if not meeting.transcript_path.exists():
            print(f"{meeting.id}: no transcript.md")
            continue
        why = _debleed_complaint(meeting)
        if why:
            print(f"{meeting.id}: left alone - {why}")
            continue

        try:
            text = meeting.transcript_path.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"referat debleed: could not read {meeting.transcript_path}: {exc}")
            continue
        lines = text.split("\n")
        # Line numbers are carried through so the removal is by position in the
        # file rather than by matching the text again later.
        entries = [
            (i, *parsed) for i, line in enumerate(lines) if (parsed := parse_entry(line))
        ]
        both = _two_channel_names(meeting)
        collapsed = _collapse(entries, settings, both, duplicates, tokens)

        short = sum(
            1 for _i, _at, label, t in entries if label in both and len(tokens(t)) < settings.min_tokens
        )
        if not collapsed:
            print(f"{meeting.id}: no duplicated remote lines ({short} too short to judge)")
            continue

        if not apply:
            print(f"\n{meeting.id}: would remove {len(collapsed)} entr(ies)\n")
            for drop, keep in collapsed:
                print(f"  - [{_hms(drop[1])}] {drop[2]}: {drop[3]}")
                print(f"  keep [{_hms(keep[1])}] {keep[2]}: {keep[3]}\n")
            print(
                f"  {len(collapsed)} of {sum(1 for e in entries if e[2] in both)} entries "
                f"under a two-channel name; {short} of those are under "
                f"{settings.min_tokens} tokens and were never eligible.\n"
                f"  Dry run. Use --apply to write."
            )
            continue

        removed = [
            {
                "at": _hms(drop[1]),
                "label": drop[2],
                "text": drop[3],
                "kept_at": _hms(keep[1]),
                "kept_label": keep[2],
            }
            for drop, keep in collapsed
        ]
        block = dict(meeting.transcription)
        # **Appended, never replaced.** A second pass can find a further pair -
        # removing one line makes two others adjacent that were not before - and
        # the first draft of this overwrote `removed` on that second pass, so
        # 67 lines were gone from the file and 1 was recorded. That destroys the
        # reversibility this whole command is justified by, which makes the
        # accumulation load-bearing rather than tidy.
        previous = meeting.transcription.get("debleed") or {}
        earlier = previous.get("removed") if isinstance(previous, dict) else None
        block["debleed"] = {
            "at": dt.datetime.now().isoformat(timespec="seconds"),
            "settings": {
                "window": settings.window,
                "contain": settings.contain,
                "back_contain": settings.back_contain,
                "min_tokens": settings.min_tokens,
            },
            "removed": (earlier if isinstance(earlier, list) else []) + removed,
        }
        meeting.transcription = block
        meeting.save()

        doomed = {drop[0] for drop, _keep in collapsed}
        kept_lines = [line for i, line in enumerate(lines) if i not in doomed]
        try:
            paths.write_text_atomic(meeting.transcript_path, "\n".join(kept_lines))
        except OSError as exc:
            print(f"referat debleed: could not write {meeting.transcript_path}: {exc}")
            return 1
        rewritten += 1
        print(f"{meeting.id}: removed {len(collapsed)} entr(ies), recorded in meta.json")

    if len(meetings) > 1:
        print(f"\n{len(meetings)} meeting(s), {rewritten} rewritten")
    return 0


def _hms(seconds: float) -> str:
    total = max(0, int(seconds))
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


def _collapse(entries, settings, both, duplicates, tokens):
    """Disjoint `(drop, keep)` pairs among the entries of one rendered transcript.

    Greedy and disjoint: once an entry has been paired it is neither removed twice
    nor used to justify a second removal, so the count printed is the count of
    lines that would actually go.

    At least one side must carry a name that resolved on both channels - see
    :func:`_debleed_complaint`. Without that gate this is a rule about text alone,
    and a rule about text alone pairs one room speaker with another.
    """
    found, used = [], set()
    for a, first in enumerate(entries):
        if a in used:
            continue
        for b in range(a + 1, len(entries)):
            if b in used:
                continue
            second = entries[b]
            if second[1] - first[1] > settings.window:
                break
            if first[2] not in both and second[2] not in both:
                continue
            if not duplicates(first[3], second[3], settings):
                continue
            keep, drop = (
                (first, second)
                if len(tokens(first[3])) >= len(tokens(second[3]))
                else (second, first)
            )
            found.append((drop, keep))
            used.update({a, b})
            break
    return found


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
    print(_code_line(current.code_mtime))
    return 0


def _code_line(code_mtime: str | None) -> str:
    """What code the running tray loaded, and whether the checkout has moved since.

    This is the half of the build stamp the tray cannot do for itself: a process
    cannot see edits made after it started, and this one is a fresh interpreter
    that reads the checkout as it is now. The tray menu shows the same timestamp
    without the comparison, which is why this line exists.
    """
    if not code_mtime:
        return "code (this tray predates the build stamp; restart it to get one)"
    try:
        loaded = dt.datetime.fromisoformat(code_mtime)
    except ValueError:
        return "code unknown"
    line = f"code {loaded.strftime(build_info.STAMP)}"
    on_disk = build_info.source_mtime()
    if build_info.outdated(loaded, on_disk) and on_disk is not None:
        # Said without the word "stale", which two lines above means a status file
        # left by a tray that died. Editing the code while the tray runs is the
        # normal way of working here; this is only the reminder that the running
        # process is not what is on disk yet.
        return f"{line} - newer code on disk ({on_disk.strftime(build_info.STAMP)}); restart the tray"
    return line


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


HOTWORD_HEADERS = ("TERM", "SOURCE")


def run_hotwords(config: Config) -> int:
    """`referat hotwords` -- the list, where each term came from, and what was cut.

    No `--json`. Nothing reads this document: the JSON verbs exist because a
    surface asked for them, and one nobody consumes would be speculative. Two JSON
    reads, so it needs no optional extra -- naming what Whisper will be told
    should not cost three gigabytes of resident torch.
    """
    from referat import hotwords

    terms = hotwords.collect(config)
    if not terms:
        print("No hotwords. A term reaches this list from one of three places:")
        print("  [transcription].hotword_extras in config.toml")
        print("  a name in the known-voices database (referat label)")
        print("  a project's glossary, hand-edited into projects.json")
        return 0

    kept, dropped = hotwords.cap(terms)
    print(render_table([(t.term, t.source) for t in kept], HOTWORD_HEADERS))
    used = sum(hotwords.estimate_tokens(t.term) for t in kept)
    print()
    print(
        f"{len(kept)} term{'s' if len(kept) != 1 else ''}, "
        f"about {used} of {hotwords.TOKEN_BUDGET} tokens of Whisper's prompt window"
    )
    if dropped:
        # Named rather than counted. A cap nobody can see is how this turns into a
        # bug report about one specific name that is never heard right.
        print(
            f"{len(dropped)} dropped by the cap, lowest priority first: "
            + ", ".join(f"{t.term} ({t.source})" for t in dropped)
        )
    return 0


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
    modes = sum(
        bool(x) for x in (args.forget, args.speaker, args.as_json, args.drop_voiceprint)
    )
    if modes > 1:
        return (
            "--forget, --speaker, --json and --drop-voiceprint are four different "
            "operations; pick one"
        )
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
    # And this one is the opposite: a voiceprint is addressed by the meeting and
    # the cluster it came from, which is the only handle on it that exists.
    if args.drop_voiceprint and not args.meeting_id:
        return "--drop-voiceprint needs the meeting id the voiceprint came from"
    if args.yes and not (args.forget or args.drop_voiceprint):
        return "--yes only answers the confirmation --forget and --drop-voiceprint ask for"
    return None


# --- Entry point ------------------------------------------------------------


def log_interrupts() -> None:
    """Log the moment a Ctrl+C *arrives*, which is not when it is felt.

    Windows delivers `CTRL_C_EVENT` to every process attached to the console, and
    Python can only turn one into a `KeyboardInterrupt` at a bytecode boundary. A
    long call into C — `ctranslate2.models.Whisper(...)`, which is minutes of
    model loading and inference — swallows the delay entirely, so the traceback
    points at wherever the interpreter next got a turn and says nothing about when
    the signal came in or how many arrived.

    That difference is the whole diagnosis. A run started from the VS Code
    terminal died six seconds in, three times, while the same command in a plain
    shell completed; the arrival time is what distinguishes something typed by a
    person from something the terminal injected on its own schedule. So the
    handler logs and then does exactly what the default one does.

    Installed in `main` and nowhere else: the tray must keep Python's default
    handling, and a library that changed signal disposition on import would be a
    trap.
    """
    started = time.monotonic()
    seen = 0

    def handler(signum: int, frame: Any) -> None:
        nonlocal seen
        seen += 1
        log.warning(
            "SIGINT #%d arrived %.1fs in; it surfaces as KeyboardInterrupt at the "
            "next bytecode boundary, which inside a model load can be seconds later",
            seen,
            time.monotonic() - started,
        )
        raise KeyboardInterrupt

    try:
        signal.signal(signal.SIGINT, handler)
    except (ValueError, OSError):
        # Not the main thread, or a platform that will not have it. The default
        # handler stays, which is the behaviour this only annotates.
        log.debug("could not install the SIGINT logger", exc_info=True)


def main(argv: list[str] | None = None) -> int:
    log_interrupts()
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

    if args.command == "show":
        return run_show(config, args.meeting_id, as_json=args.as_json)

    if args.command == "transcript":
        return run_transcript(config, args.meeting_id, as_json=args.as_json)

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

    if args.command == "debleed":
        return run_debleed(config, args.meeting_id, apply=args.apply)

    if args.command == "relabel":
        return run_relabel(config, args.meeting_id)

    if args.command == "delete":
        return run_delete(config, args.meeting_id, assume_yes=args.assume_yes)

    if args.command == "devices":
        return run_devices(config)

    if args.command == "hotwords":
        return run_hotwords(config)

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
        if args.drop_voiceprint:
            assert args.meeting_id
            return labelling.run_drop_voiceprint(
                config, args.meeting_id, args.drop_voiceprint, assume_yes=args.yes
            )
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
