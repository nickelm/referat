"""The `referat` command line interface.

`config` since build step 1, `label` since 7b, `list`, `rerun` and `status`
since step 8, `devices` since the first USB microphone, `index` with the
meetings folder scaffold at step 10, `hotwords` at step 12b, `project`, `tag`,
`untag` and `state` at step 14, `promote` at step 15, and `people` at step 20's
phase 5. Between them they are
the whole of Referat that is not the tray: what has been recorded, what still
needs a name, who Referat knows a name for, what work each meeting belongs to,
what the tray is doing, what it
will record with, what Whisper is told about before it transcribes, how to
transcribe a meeting again, how to accept a transcript the quality gate refused,
and how to rebuild the dashboard over all of it.

**The CLI owns every mutation, and is the only implementation of any of it.**
The projects file, the tag logic, the lifecycle vocabulary and the rules about
what a name may be live in Python exactly once. The tray and the command center
import the same functions, because they are already a Python process inside this
package. The rule is one implementation, not one process boundary — which is
worth saying in both directions, since a later reader could "fix" it either way.

**The `--json` documents outlived their first reader and stay.** Every one of
them was written for the VS Code extension, which shelled out to these commands
because it was TypeScript and had no other way in; it was deleted at build step
23. They stay because they are the shape a surface and the CLI agree on, and a
document you can print is a document you can test — and because they are now what
`referat show`, `referat transcript` and `referat people` hand *anybody* who asks
in a script.

**Light by default.** Only `rerun` needs the `transcribe` extra, and it imports
it inside :func:`referat.rerun.run`, so `list` and `status` answer instantly
without three gigabytes of torch. `project`, `tag`, `untag`, `state`, `people` and `hotwords` are a
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
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

from referat import __version__, actions, build_info, index, paths, projects, status, voices
from referat.config import Config, ConfigError, load_config
from referat.meeting import (
    Meeting,
    MeetingStatus,
    format_duration,
    load_meetings,
    resolve_meeting,
)

if TYPE_CHECKING:
    # Only for the annotation on `_inactive`. The runtime import stays inside
    # `people_document`, where it has always been.
    from referat import people as people_mod

log = logging.getLogger(__name__)

NEEDS_CONFIG = (
    "actions",
    "config",
    "day",
    "debleed",
    "delete",
    "devices",
    "hotwords",
    "index",
    "label",
    "list",
    "notes",
    "people",
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
        help="emit the same meetings as JSON",
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
        help="emit the same answer as JSON",
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

    hotwords_parser = subcommands.add_parser(
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
    hotwords_parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="emit the merged list, its sources and what the cap dropped as JSON",
    )

    notes_parser = subcommands.add_parser(
        "notes",
        help="write notes.md for a meeting, by running /cleanup on it",
        description=(
            "Run the /cleanup slash command over one meeting's transcript and "
            "write notes.md beside it, then record notes-written in meta.json. "
            "Referat summarises nothing itself: this spawns the official claude "
            "binary under your own Claude Code login, in the meetings folder, "
            "where the prompt and the rule denying it every read of .voices/ "
            "both live. No API key is involved, here or anywhere in Referat. "
            "The prompt is a versioned Markdown file in that folder, so what a "
            "note says is changed by editing it rather than by changing code."
        ),
    )
    notes_parser.add_argument("meeting_id", help="e.g. 2026-08-27_1400")

    people_parser = subcommands.add_parser(
        "people",
        help="everybody Referat knows a name for, and where they appear",
        description=(
            "One row per known name: how many voiceprints are filed under it, the "
            "meetings those prints came from, the meetings the name appears in, and "
            "the projects those meetings carry. The two directions differ - a "
            "person recognised automatically files nothing new, and a rerun can "
            "renumber past somebody whose print keeps the provenance the meeting "
            "record lost - so both are shown. A name a transcript still uses that "
            "the database holds nothing under is listed too, with no prints, "
            "because nothing else compares those two files. "
            "Names, counts and meeting ids only: no embedding and no path into "
            "the voices database is printed, here or in --json. "
            "`referat label --forget <name>` is how a person is deleted."
        ),
    )
    people_parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="emit the same people as JSON, for the command center",
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
    # The three flags below were built for the VS Code extension's labeling
    # webview and outlived it. They exist because the prompt above cannot be
    # driven from a subprocess: it reads `input()`, and `--forget`'s confirmation
    # answers *no* on EOF, so a caller with no terminal is told "Nothing was
    # deleted." The command center calls `label.name_speaker` directly and needs
    # none of them; anything scripting this from outside Python still does.
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

    day = subcommands.add_parser(
        "day",
        help="write a short summary of one day's meetings, from their notes",
        description=(
            "Spawns the official claude binary with /standup in the meetings "
            "folder, which reads that day's notes.md files and writes "
            "days/<date>.md - a glance at what happened, not a digest. Re-run it "
            "whenever another meeting that day gets notes; it overwrites."
        ),
    )
    day.add_argument(
        "day", nargs="?", default="", help="the date, YYYY-MM-DD (default: today)"
    )

    _add_actions_parser(subcommands)
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



def _add_actions_parser(subcommands: argparse._SubParsersAction) -> None:
    """`referat actions [<verb>]` — the list, and the five things you can do to one.

    Nested subparsers in the one argparse block, exactly as
    :func:`_add_project_parser` builds them. The one difference is that **the
    verb is optional and defaults to the listing**, because `referat project` is
    a namespace of operations while `referat actions` is a question somebody is
    asking; see :func:`run_actions`.
    """
    parent = subcommands.add_parser(
        "actions",
        help="the action items in your meeting notes, and what you have done about them",
        description=(
            "Action items are parsed out of each meeting's notes.md - the "
            "`## Action items` section /cleanup writes - and never re-extracted. "
            "Ticking, correcting and dropping one is recorded in actions.json "
            "beside the notes; none of it edits notes.md, so a meeting's record "
            "of what it produced stays exactly as /cleanup wrote it."
        ),
    )
    parent.add_argument("--person", default="", help="whose items to show (default: you)")
    parent.add_argument(
        "--everyone", action="store_true", help="every owner, not just one person"
    )
    parent.add_argument("--meeting", default="", help="only this meeting's items")
    parent.add_argument("--done", action="store_true", help="include what is already ticked")
    parent.add_argument("--json", action="store_true", dest="as_json", help="the whole document")
    parent.add_argument(
        "--markdown",
        action="store_true",
        dest="as_markdown",
        help="a `- [ ]` task list, the same text the command center's Copy button gives",
    )
    verbs = parent.add_subparsers(dest="actions_command")

    for verb, help_text in (
        ("done", "tick an item off"),
        ("undone", "put a ticked item back"),
        ("drop", "hide an item that was never really one - notes.md is not touched"),
        ("restore", "bring a dropped item back"),
    ):
        one = verbs.add_parser(verb, help=help_text)
        one.add_argument("key", help="the item's key, or a unique prefix of it")

    edit = verbs.add_parser(
        "edit", help="correct an item's wording; no text reverts to what the notes say"
    )
    edit.add_argument("key", help="the item's key, or a unique prefix of it")
    edit.add_argument("text", nargs="*", help="the corrected wording")

    prune = verbs.add_parser(
        "prune", help="forget stored state whose item no longer appears in any notes.md"
    )
    prune.add_argument(
        "key", nargs="*", help="which orphans to forget (default: all of them)"
    )


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

    describe = verbs.add_parser(
        "describe",
        help="read or set a project's one-line description",
        description=(
            "One line about what this thread of work is. With no text it prints "
            "what is there. The empty string clears it, and so does --clear."
        ),
    )
    describe.add_argument("project_id")
    describe.add_argument("description", nargs="?", help="the new description")
    describe.add_argument(
        "--clear", action="store_true", help="remove the description"
    )

    glossary = verbs.add_parser(
        "glossary",
        help="the terms of art, product names and people belonging to this project",
        description=(
            "A project's glossary is read twice, at two different times. Every "
            "glossary is merged into the one global hotword list handed to "
            "faster-whisper, which acts before any meeting has been tagged; and "
            "once a meeting is tagged, the glossaries of all its tags are what "
            "/cleanup normalizes its notes against. With no flags this prints the "
            "list. `referat hotwords` shows what the merge did with it, including "
            "anything the 223-token cap dropped."
        ),
    )
    glossary.add_argument("project_id")
    glossary.add_argument(
        "--add", nargs="+", metavar="TERM", help="terms to add, in the order given"
    )
    glossary.add_argument(
        "--remove",
        nargs="+",
        metavar="TERM",
        help="terms to take out, matched without regard to case",
    )
    glossary.add_argument("--clear", action="store_true", help="empty the glossary")

    archive = verbs.add_parser(
        "archive",
        help="hide a finished project from the tag picker, keeping every tag it has",
        description=(
            "Archiving is a presentation decision and the whole of it. The project "
            "stays in projects.json, every meeting keeps its tag and that tag still "
            "resolves to this name, and the glossary still feeds the hotword list "
            "Whisper is handed. What changes is that the tag picker stops offering "
            "it and the projects page gives it a section of its own. `referat tag` "
            "will still add it: an archived project is not an orphan, and a late "
            "meeting belonging to a finished thread of work is exactly the case. "
            "This is the answer to a project that is finished; `project rm` is the "
            "answer to one that was a mistake, and it orphans every tag it had."
        ),
    )
    archive.add_argument("project_id")

    unarchive = verbs.add_parser(
        "unarchive",
        help="put an archived project back among the live ones",
        description=(
            "Clears `archived_at` and nothing else. Nothing was lost by archiving, "
            "so nothing has to be restored."
        ),
    )
    unarchive.add_argument("project_id")

    listing = verbs.add_parser("list", help="every project, and how many meetings carry it")
    listing.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="emit the same projects as JSON",
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
    """Every meeting, as one document. The listing every surface is built on.

    Written for the VS Code extension at step 11 and now the command center's,
    which reads it once per refresh and hands it to the meetings list, the
    dashboard's three queues and the actions tab. It carries six things a caller
    would otherwise work out for itself: the two roots, the duration formatter,
    `audio_state`, the title rule, the staged/promoted distinction, and which
    speakers are still numbers. All six already exist exactly once in Python and
    are shared by three or more callers *so that they cannot disagree* — so a
    surface is given the answers rather than the ingredients. A second
    implementation anywhere would be a seventh reader of `meta.json` with its own
    opinions about all of them.

    Deliberately not sorted here: this is `referat list`'s document, so it keeps
    `list`'s oldest-first order, and the dashboard that wants newest-first
    reverses it the way `referat.index` already does.

    The top-level `projects` block is the seventh thing, added at step 14: the map
    from a tag id to what a person reads. It comes out of the same
    `ProjectsDB.name_map` that `referat project list --json` hands out, so the
    join between a tag and its name cannot drift either. An id in a meeting's
    `tags` that is missing from this map is an orphan, and that absence is a
    surface's cue to render it as one.
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


LIVE = ("recording", "transcribing")
"""The two lifecycle values that mean a meeting has not finished happening.

Neither is *pending work*: there is nothing anybody can do about either but
wait, so they are held out of every queue :func:`pending` builds. The same
distinction `run_list`'s footer makes when it refuses to suggest a `rerun` for a
meeting that is still being recorded — advice about a live meeting is the worst
advice either surface could give.
"""


def pending(document: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """The three queues of work somebody still owes, over a `list_document`.

    Pure, and deliberately over the *document* rather than over `Meeting`
    objects: it opens no file, so a caller already holding the listing — the
    command center holds one for its meetings tab, and its dashboard is built
    from these three lists — pays nothing at all for them.

    They are in the order the work is done in, which is the flow rule this UI
    already follows: **tag, then label, then write notes**. Tagging first is what
    narrows the gallery a person is offered at labeling time; notes last because
    `/cleanup` reads a transcript whose speakers are worth having names by then.

    `notes` is the one queue with a condition that is about *reach* rather than
    about work: `/cleanup` runs with its working directory at the meetings folder
    and is handed a meeting id, so a meeting still in staging is not somewhere it
    can look. That is the same test :meth:`referat.ui.window.CommandCenter._on_notes_all`
    used to make for itself, which is why it now asks here instead — a queue and
    the button that drains it disagreeing about what is in it is exactly the
    second implementation this codebase keeps deleting.
    """
    meetings = [m for m in document["meetings"] if m["status"] not in LIVE]
    return {
        "untagged": [m for m in meetings if not m["tags"]],
        "unnamed": [m for m in meetings if m["unnamed"]],
        "notes": [
            m for m in meetings if m["transcript"] and not m["notes"] and not m["staged"]
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

    # Not `pending`, which is the module-level function two definitions above: a
    # local of that name, inside the one command that prints these same counts,
    # is exactly where somebody would later reach for the queues and get an int.
    unnamed = sum(1 for row in rows if row[4] != "-")
    summary = f"\n{len(meetings)} meeting{'s' if len(meetings) != 1 else ''}"
    if unnamed:
        summary += f", {unnamed} with unnamed speakers - run: referat label"
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

    `markdown` is the file itself, unparsed. It costs nothing — this function has
    already read it — and it is what a **copy** out of the command center's
    transcript pane hands over, so that a copy is the source rather than a
    rendering of it. Carrying it here is what keeps the window from opening
    `transcript.md` for itself: it reads exactly one file, `notes.md`, and the
    shape of everything else it shows is Python's to describe.

    `people` is the subset of those labels that are somebody's *name*, decided by
    :func:`referat.voices.name_complaint` — the one function that knows `ME` and
    `REMOTE` are channel labels and `SPEAKER_NN` is a number. Phase 5 needs it so
    the command center can turn a name in the label column into a link to that
    person without a second copy of that rule in the UI, which is the same reason
    `unnamed` is computed here rather than in any surface.
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
        "people": [label for label in labels if voices.name_complaint(label) is None],
        "markdown": text,
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


PROJECT_HEADERS = ("ID", "NAME", "MEETINGS", "DOCS", "ARCHIVED")
PROJECT_RIGHT_ALIGNED = (2, 3)


def project_document(config: Config) -> dict[str, Any]:
    """`referat project list --json`: every project, and how much hangs off it.

    `names` is :meth:`referat.projects.ProjectsDB.name_map`, the very map
    :func:`list_document` embeds — so a tag chip resolves identically whichever
    of the two documents its surface happens to be holding.

    `orphans` is the other half of the same question: ids that meetings still
    carry and no project answers to. They are counted here rather than left for
    each reader to derive, which is the whole habit this step is about.

    **`complaint` is why the list is empty when it is not really empty.** A
    `projects.json` that will not parse loads as no projects at all, which is the
    rule that keeps a broken file from costing a transcript — and it means an
    empty `projects` here has two causes that look identical. Every other reader
    of this document had to guess between them; phase 4's projects page is the
    first that must not, since it offers to *create* a project into a file whose
    contents it cannot see. Empty when the file is fine.

    **Each project is spread from its own `to_json`, so a new field arrives here
    for free** — `archived_at` did, at build step 21, with no change to this
    function. That is a property worth relying on rather than an accident: the
    dataclass is the schema, and a document that re-listed the keys would be a
    second place to forget one. It is also why there is no top-level `archived`
    list beside `names`: the only reader iterates `projects`, and a second copy
    of the same fact is a second thing that can disagree.
    """
    db = projects.ProjectsDB.load(config)
    counts = projects.tag_counts(load_meetings(config))
    return {
        "projects_file": str(projects.projects_path(config)),
        "complaint": _unreadable_complaint(db.path, CANNOT_LOOK_UP) if db.unreadable else "",
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
    if document["complaint"]:
        # Said rather than rendered as an empty list, which is what this printed
        # before phase 4 gave the document somewhere to say it: an unreadable
        # file and a file with nothing in it are the same picture and must not
        # get the same sentence.
        print(f"referat project list: {document['complaint']}", file=sys.stderr)
        return 1
    entries = document["projects"]
    if not entries:
        print("No projects yet. Create one with: referat project add <name>")
        return 0

    rows = [
        (
            p["id"],
            p["name"],
            str(p["meetings"]),
            str(len(p["docs"])) if p["docs"] else "-",
            # The date rather than a tick, and the same date-or-dash shape DOCS
            # already has one column along. A tick column would be the one thing
            # on a page carrying no information; when a project stopped is worth
            # the width, and it is the only place that date is ever printed.
            p["archived_at"][:10] or "-",
        )
        for p in entries
    ]
    print(render_table(rows, PROJECT_HEADERS, PROJECT_RIGHT_ALIGNED))
    print(f"\n{len(rows)} project{'s' if len(rows) != 1 else ''}")

    archived = [p for p in entries if p["archived_at"]]
    if archived:
        # Listed in the table rather than hidden behind this line: `project list`
        # is the inventory, and it is where somebody comes to see what the file
        # actually holds. The line says the one consequence the column cannot.
        print(
            f"{len(archived)} archived project{'s' if len(archived) != 1 else ''}, "
            f"not offered by the tag picker; their tags still resolve and their "
            f"glossaries still feed the hotword list. Bring one back with: "
            f"referat project unarchive <id>"
        )

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


def project_names(config: Config) -> tuple[dict[str, str], set[str], str]:
    """Every project id, what it is called, which are archived, or the complaint.

    The tag picker's whole reading vocabulary. It needs the map **fresh every
    time it opens** — the sidebar's picker took it from a cached listing and
    offered one of two projects, because nothing invalidated that cache — and it
    may not open `projects.json` itself, so this is the one call that answers
    both.

    Deliberately not :func:`project_document`, which answers a superset and pays
    a full `load_meetings` scan of both roots for the per-project meeting counts.
    A picker wants a map of names, and this is one file read.

    The complaint is :data:`CANNOT_TAG`, because that is what this is: with no
    names loaded every tag would render as an orphan, which is a worse thing to
    show than a refusal.

    The archived ids come back **beside** the map rather than subtracted from it,
    for the reason :meth:`referat.projects.ProjectsDB.archived_ids` gives: a
    picker hides an archived project it is *offering*, and still has to render
    one the meeting already **carries** under its real name. A narrowed map would
    make that row an orphan, which is a lie about a project that exists.
    """
    db = projects.ProjectsDB.load(config)
    if db.unreadable:
        return {}, set(), _unreadable_complaint(db.path, CANNOT_TAG)
    return db.name_map(), db.archived_ids(), ""


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
        return None, _unreadable_complaint(db.path, WOULD_OVERWRITE)
    complaint = projects.name_complaint(name)
    if complaint:
        return None, f"a project name {complaint}"
    project = db.add(name)
    db.save()
    return project, ""


def run_project(config: Config, args: argparse.Namespace) -> int:
    """`referat project <verb>`. Every verb here is a JSON read and a JSON write.

    **Every mutating verb is one line of dispatch onto a guarded function**, and
    none of them opens `projects.json` for itself. That is phase 4's correction:
    `rename` and `rm` used to load the database here, check it here and save it
    here, so the command center's projects page would have had to grow a second
    copy of all three to do the same thing. The verbs now say what to print and
    nothing else — an :class:`Outcome`'s message, prefixed with the command.
    """
    if args.verb is None:
        print(
            "referat project: pick a verb - add, rename, describe, glossary, "
            "archive, unarchive, rm or list",
            file=sys.stderr,
        )
        return 2

    if args.verb == "list":
        return run_project_list(config, as_json=args.as_json)

    if args.verb == "add":
        project, complaint = create_project(config, args.name)
        if project is None:
            print(f"referat project add: {complaint}", file=sys.stderr)
            return 1
        if getattr(args, "as_json", False):
            print(json.dumps(project.to_json(), indent=2))
            return 0
        print(f"{project.id}  {project.name}")
        return 0

    if args.verb == "rename":
        return _report(rename_project(config, args.project_id, args.name), "project rename")

    if args.verb == "archive":
        return _report(set_archived(config, args.project_id, True), "project archive")

    if args.verb == "unarchive":
        return _report(set_archived(config, args.project_id, False), "project unarchive")

    if args.verb == "rm":
        return _report(remove_project(config, args.project_id), "project rm")

    if args.verb == "describe":
        return run_project_describe(config, args)

    if args.verb == "glossary":
        return run_project_glossary(config, args)

    print(f"referat project: unknown verb {args.verb}", file=sys.stderr)
    return 2


def _report(outcome: Outcome, command: str) -> int:
    """Print an :class:`Outcome` as a command prints one, and become its exit code.

    The prefix is added here rather than baked into the message, for the reason
    :class:`Outcome` records: it is direction-dependent, and the command center
    calls these same functions with no command to name.
    """
    if outcome.ok:
        print(outcome.message)
        return 0
    print(f"referat {command}: {outcome.message}", file=sys.stderr)
    return 1


def run_project_describe(config: Config, args: argparse.Namespace) -> int:
    """`referat project describe <id> [text]` — read it, set it, or clear it.

    With no text it prints what is there, because a `project list` shows no
    descriptions and this is where somebody comes to read one. Setting the empty
    string is how a description is removed, and `--clear` is that spelled out so
    a shell cannot swallow it.
    """
    if args.description is None and not args.clear:
        project, complaint = _read_project(config, args.project_id)
        if project is None:
            print(f"referat project describe: {complaint}", file=sys.stderr)
            return 1
        print(project.description or f"{project.id} has no description")
        return 0
    text = "" if args.clear else str(args.description)
    return _report(set_description(config, args.project_id, text), "project describe")


def run_project_glossary(config: Config, args: argparse.Namespace) -> int:
    """`referat project glossary <id>` — print it, or add to, remove from or clear it.

    Every writing form ends in one :func:`set_glossary` call over the **whole**
    list, which is why the current terms are read here first. `--add` and
    `--remove` are ergonomics over a replacement rather than a second kind of
    write: a diff that reached the file would be a second implementation of what
    a text box in the command center does in one go.

    Removal is case-insensitive, matching the deduplication that put the terms
    there. A term typed in the wrong case is a term somebody means, and being
    told nothing was removed because they wrote `Elmqvist` for `elmqvist` would
    be the tool insisting on a distinction it does not itself keep.
    """
    project, complaint = _read_project(config, args.project_id)
    if project is None:
        print(f"referat project glossary: {complaint}", file=sys.stderr)
        return 1

    if not (args.add or args.remove or args.clear):
        if not project.glossary:
            print(
                f"{project.id} has an empty glossary. Add to it with: "
                f"referat project glossary {project.id} --add <term>..."
            )
            return 0
        for term in project.glossary:
            print(term)
        return 0

    if args.clear:
        terms: list[str] = []
    else:
        dropping = {term.casefold() for term in args.remove or ()}
        terms = [t for t in project.glossary if t.casefold() not in dropping]
        terms.extend(args.add or ())
    return _report(set_glossary(config, args.project_id, terms), "project glossary")


def _read_project(config: Config, pid: str) -> tuple[projects.Project | None, str]:
    """One project, for reading, or the complaint that stopped it.

    The reading counterpart of :func:`_open_project`, and separate from it because
    the guard differs where it matters: an unreadable file complains in
    :data:`CANNOT_LOOK_UP` here, since nothing is about to be overwritten.
    """
    db = projects.ProjectsDB.load(config)
    if db.unreadable:
        return None, _unreadable_complaint(db.path, CANNOT_LOOK_UP)
    project = db.projects.get(pid)
    if project is None:
        return None, _unknown_complaint(db, [pid])
    return project, ""


@dataclass(frozen=True)
class Outcome:
    """What a mutation did, in the words of whoever owns the rule.

    A refusal reaches the user as the sentence the rule's owner wrote, never as
    something the caller paraphrased. The deleted VS Code extension got that
    guarantee by reading the CLI's stderr and could get it no other way; the
    command center gets it as this, and the guarantee is the part that mattered
    rather than the mechanism.

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


WOULD_OVERWRITE = "so writing now would replace everything in it"
CANNOT_TAG = "so nothing can be tagged until it is"
CANNOT_LOOK_UP = "so no project can be looked up until it is"
CANNOT_TICK = "so no action item's state can be changed until it is"
"""The consequences of a state file that will not parse: three for `projects.json`, one for `actions.json`.

One sentence template, three endings, and the ending is the caller's to choose
because it is the only part that differs by what was being attempted. Phase 4 is
what made that a real distinction: `referat project glossary` reads this file to
print a list, and telling somebody nothing can be *tagged* would be an answer to
a question they did not ask.
"""


def _unreadable_complaint(path: Path, consequence: str) -> str:
    """Why a command stops when it cannot tell an empty state file from a broken one.

    An unreadable file loads as *no projects*. That is right for reading — the
    meetings table still renders, with every tag shown as an orphan — and
    catastrophic for writing, because the next save would replace a file full of
    projects with an empty list and nothing would say so. `tag` does not write it
    and is stopped anyway: with no ids loaded it would reject every id as unknown
    and report a typo nobody made.

    Takes the **path** rather than a database, because `actions.json` runs under
    exactly this rule and says exactly this sentence — see
    :data:`CANNOT_TICK`. One template, and each store supplies its own ending.

    The parse error itself is in the log; this says what to do about it.
    """
    return f"{path} exists but could not be read, {consequence}. Fix or delete that file first."


def _unknown_complaint(db: projects.ProjectsDB, ids: Sequence[str]) -> str:
    """Ids nothing resolves, and what would have worked. One id or many, one sentence."""
    known = ", ".join(sorted(db.projects)) or "none yet"
    return f"no project {', '.join(ids)} (known: {known})"


def _open_project(
    config: Config, pid: str
) -> tuple[projects.ProjectsDB | None, projects.Project | None, str]:
    """Load the projects file for a write and find one project in it.

    The two guards every project mutation runs first, in the order they have to
    run in: an unreadable file before an unknown id, because a complaint about a
    typo is advice about the wrong problem when the file that would answer it did
    not parse. :data:`WOULD_OVERWRITE` throughout — a caller here is about to
    save, and saving over an unreadable file replaces everything in it.

    Returns the database as well as the project, because every caller saves.
    """
    db = projects.ProjectsDB.load(config)
    if db.unreadable:
        return None, None, _unreadable_complaint(db.path, WOULD_OVERWRITE)
    project = db.projects.get(pid)
    if project is None:
        return db, None, _unknown_complaint(db, [pid])
    return db, project, ""


def rename_project(config: Config, pid: str, name: str) -> Outcome:
    """Change a project's display name. The only implementation of renaming one.

    `referat project rename` is this, and so is the command center's projects
    page. The id does **not** move, which is said out loud in the message because
    it is the surprising half and the half every `meta.json` depends on.
    """
    db, project, complaint = _open_project(config, pid)
    if project is None or db is None:
        return Outcome(False, complaint)
    name_complaint = projects.name_complaint(name)
    if name_complaint:
        return Outcome(False, f"a project name {name_complaint}")
    db.rename(pid, name)
    db.save()
    return Outcome(True, f"{project.id} is now called {project.name} (the id does not change)")


def set_description(config: Config, pid: str, description: str) -> Outcome:
    """Set a project's one-line description. The only implementation of that.

    No name rules apply: a description is prose about a thread of work and never
    becomes an id. An empty string clears it, which is the honest way to say
    "there is nothing to say about this project" and needs no separate verb.
    """
    db, project, complaint = _open_project(config, pid)
    if project is None or db is None:
        return Outcome(False, complaint)
    db.describe(pid, description)
    db.save()
    if not project.description:
        return Outcome(True, f"{project.id} has no description")
    return Outcome(True, f"{project.id}: {project.description}")


def set_glossary(config: Config, pid: str, terms: Sequence[str]) -> Outcome:
    """Replace a project's glossary. The only implementation of that.

    **A replacement, unlike :func:`apply_tags`**, and the difference is the same
    one :meth:`referat.projects.ProjectsDB.set_glossary` records: a tag picker
    renders a subset of the projects, so a replacement there could drop a tag it
    never drew, while a glossary is edited as the whole list and the caller has
    all of it in hand. `referat project glossary --add` reads the list and hands
    back the whole of it for exactly that reason.

    The message names what the file now holds rather than what changed. A
    glossary is short, the whole of it fits on one line, and the next question
    after editing one is always what the list is now — which is also what
    `referat hotwords` will merge.
    """
    db, project, complaint = _open_project(config, pid)
    if project is None or db is None:
        return Outcome(False, complaint)
    db.set_glossary(pid, terms)
    db.save()
    if not project.glossary:
        return Outcome(True, f"{project.id} has an empty glossary")
    return Outcome(
        True,
        f"{project.id}: {len(project.glossary)} term(s) - {', '.join(project.glossary)}",
    )


def set_archived(config: Config, pid: str, archived: bool) -> Outcome:
    """Archive or unarchive a project. The only implementation of both directions.

    **One guarded function taking a direction, not two.** The other five project
    operations are five different things that do not pair; this one is a single
    flag, and the settled shape for that is already in this file twice —
    :func:`apply_tags` takes `add` and `remove` together with `run_tag` and
    `run_untag` as thin wrappers, and :func:`set_action_done` and
    :func:`dismiss_action` are boolean-direction guarded functions. Two functions
    here would be two copies of the same open, mutate, save and report differing
    in one boolean and one sentence.

    **Archiving hides and never removes**, which is the whole design and is what
    the message has to say: the tags stay on their meetings and still resolve to
    this name, the glossary still feeds the hotword list, and `referat tag` will
    still add it. What stops is the *offering*. Deliberately no meeting count
    beside that, unlike :func:`remove_project`: that costs a `load_meetings` scan
    of both roots, which a delete pays for because it is irreversible and the
    orphans are the one thing somebody would not otherwise see. Nothing here is
    lost, so nothing has to be counted.

    Both no-ops come back `ok` **having written nothing** — the case
    :class:`Outcome` exists to describe. The already-archived message names the
    date, which is what makes a no-op distinguishable from a write at a prompt,
    the same reason :func:`_tag_lines` says both halves of an idempotent change.
    """
    db, project, complaint = _open_project(config, pid)
    if project is None or db is None:
        return Outcome(False, complaint)
    if archived and project.archived:
        return Outcome(True, f"{project.id} was already archived, on {project.archived_at}")
    if not archived and not project.archived:
        return Outcome(True, f"{project.id} is not archived")

    db.set_archived(pid, archived)
    db.save()
    if not archived:
        return Outcome(True, f"{project.id} ({project.name}) is active again")
    return Outcome(
        True,
        f"Archived {project.id} ({project.name}).\n"
        f"Every meeting keeps its tag and it still resolves to that name, and the "
        f"glossary still feeds the hotword list; `referat tag` will still add it. "
        f"What changes is that the tag picker stops offering it. "
        f"Bring it back with: referat project unarchive {project.id}",
    )


def remove_project(config: Config, pid: str) -> Outcome:
    """Delete a project, and say what it left behind. The only implementation of that.

    Cascades nothing: no `meta.json`, no `notes.md` and no Google Doc is touched,
    so the meetings carrying this id keep carrying it as orphans. The count of
    them is in the message rather than in the log, because it is the one
    consequence somebody would otherwise not see — and the `referat untag` that
    clears them is spelled out beside it.

    The meeting scan happens **after** the save, as `referat project rm` has
    always done it: it is a scan of both roots for a sentence, and a delete that
    failed to report its orphans is better than one that refused to happen.
    """
    db, project, complaint = _open_project(config, pid)
    if project is None or db is None:
        return Outcome(False, complaint)
    db.remove(pid)
    db.save()
    lines = [f"Deleted {project.id} ({project.name})."]
    carrying = projects.tag_counts(load_meetings(config)).get(project.id, 0)
    if carrying:
        lines.append(
            f"{carrying} meeting{'s' if carrying != 1 else ''} still carr"
            f"{'y' if carrying != 1 else 'ies'} that id as an orphaned tag; "
            f"nothing else was touched. Remove them with: referat untag <id> {project.id}"
        )
    return Outcome(True, "\n".join(lines))


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

    **An archived project is still tagged, deliberately, and this says so.** The
    refusal above is written narrowly on purpose, and an archived id *does*
    answer to a project — it is not an orphan, so refusing it would widen that
    guard past the reason written beside it. It is *a missing tag must never cost
    a name* said one entity along: an archived project must never cost a tag, and
    a late meeting belonging to a finished thread of work is exactly the case
    that would otherwise take three writes. Hiding is what a surface does; a verb
    does not inherit it. The note lives here rather than in `run_tag`, because a
    `run_*` holding a rule is a `run_*` a second surface cannot use — and it
    costs no extra read, being computed inside the `if add:` block where the
    database is already open, so a pure removal still never opens the file.
    """
    add, remove = list(add), list(remove)
    archived: set[str] = set()
    if add:
        db = projects.ProjectsDB.load(config)
        if db.unreadable:
            return Outcome(False, _unreadable_complaint(db.path, CANNOT_TAG))
        unknown = [pid for pid in add if pid not in db.projects]
        if unknown:
            # Refused, unlike a removal: an id no project answers to is an
            # orphan, and the one way to make one on purpose should be deleting a
            # project rather than mistyping at a prompt.
            return Outcome(False, _unknown_complaint(db, unknown))
        archived = db.archived_ids()

    meeting, why = resolve_meeting(config, meeting_id)
    if meeting is None:
        return Outcome(False, why)

    added = projects.add_tags(meeting, add) if add else []
    removed = projects.remove_tags(meeting, remove) if remove else []
    if added or removed:
        meeting.save()
    # Keyed on what actually changed rather than on what was asked for, so
    # re-tagging an already-tagged archived project says nothing new.
    newly_archived = [pid for pid in added if pid in archived]
    lines = [
        *_tag_lines(meeting.id, added, add, "tagged", "already tagged"),
        *_tag_lines(meeting.id, removed, remove, "untagged", "was not tagged"),
    ]
    if newly_archived:
        lines.append(
            f"{meeting.id}: {', '.join(newly_archived)} archived - the tag "
            f"resolves and stays; the tag picker does not offer it"
        )
    # Printed once and last, whatever happened above.
    lines.append(f"tags: {', '.join(meeting.tags) if meeting.tags else 'none (untagged)'}")
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
    outcome = set_notes_written(config, meeting_id)
    return _report(outcome, "state")


def set_notes_written(config: Config, meeting_id: str) -> Outcome:
    """Record that `notes.md` has been written. The only implementation.

    Split out of :func:`run_state` when the command center grew a *Generate
    notes* button: `/cleanup` is forbidden from touching `meta.json`, so whoever
    spawned it says so afterwards, and there are now two whoevers. The same
    correction as `apply_tags`, `name_speaker`, the five project functions and
    `forget_person` — the primitive is `meeting.save()`, and what makes this
    *correct* is the transition guard and the dashboard regeneration around it.

    Already-`notes_written` is an `ok` outcome and not a refusal: re-running
    `/cleanup` on a meeting that already has notes is an ordinary thing to do,
    and the second run must not report a failure for a state that is exactly
    what the caller wanted.
    """
    meeting, why = resolve_meeting(config, meeting_id)
    if meeting is None:
        return Outcome(False, why)

    if meeting.status is MeetingStatus.NOTES_WRITTEN:
        # The dashboard still wants rebuilding: a second `/cleanup` rewrites
        # `notes.md`, which is where the title comes from.
        index.write_index(config)
        return Outcome(True, f"{meeting.id} is already {meeting.status}")
    if meeting.status is not MeetingStatus.TRANSCRIBED:
        return Outcome(
            False,
            f"{meeting.id} is {meeting.status}, and {NOTES_WRITTEN_VERB} is only "
            f"reachable from {MeetingStatus.TRANSCRIBED}",
        )

    meeting.status = MeetingStatus.NOTES_WRITTEN
    meeting.save()
    # The dashboard prints the status and takes its title from the H1 of the
    # notes that have just appeared, so both are stale until this runs.
    index.write_index(config)
    return Outcome(True, f"{meeting.id}: {meeting.status}")


# --- referat promote --------------------------------------------------------


def promote_warning(config: Config, meeting: Meeting) -> str:
    """What somebody is told *before* the audio goes, as one block of prose.

    The counterpart of :func:`delete_warning`, added at build step 23 when the
    command center grew the off-ramp the sidebar had, and the same exception for
    the same reason: it is spoken *before* the command runs, so there is no
    outcome to quote. Said here rather than in the modal so the prompt's refusal
    and the window's question cannot come to describe one irreversible act
    differently.

    What it has to say that :func:`delete_warning` does not is that this is
    irreversible in a way deleting a whole meeting is not. Deleting takes the
    transcript with the audio; this **keeps** the transcript and destroys the
    only material it could ever be re-derived from — which matters most for
    exactly the meeting this button is normally pressed on, one whose transcript
    the quality gate was not confident in.
    """
    kept = [p for p in (meeting.mic_path, meeting.system_path) if p.exists()]
    lines = [f"{meeting.dir}  ->  {config.paths.meetings_dir}"]
    if kept:
        megabytes = sum(p.stat().st_size for p in kept) / 1e6
        names = ", ".join(p.name for p in kept)
        lines.append(
            f"{names} ({megabytes:.1f} MB) will be deleted permanently. No WAV may "
            f"ever enter the meetings folder, so releasing the audio is what "
            f"promoting costs."
        )
        lines.append(
            "transcript.md is all that will be left, and there will be nothing to "
            "re-transcribe from. This cannot be undone."
        )
    else:
        lines.append(
            "No audio left to delete - this only moves the folder, which is the "
            "retry for a promotion that failed to move it the first time."
        )
    if meeting.status is MeetingStatus.GATE_FAILED:
        lines.append(
            "The quality gate was not confident in this transcript. Promoting is "
            "accepting it, so the meeting becomes `transcribed`."
        )
    elif meeting.transcription.get("audio_kept"):
        lines.append(
            "This audio was kept on request rather than by a failed gate - "
            "[transcription].keep_audio - so a rerun would simply keep it again."
        )
    return "\n".join(lines)


def promote_meeting(config: Config, meeting_id: str, *, release_audio: bool) -> Outcome:
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

    **The only implementation**, since build step 23. It was `run_promote`'s
    whole body until the command center needed the same off-ramp the sidebar had,
    which is the ninth time a rule turned out to be sitting inside a `run_*` that
    a second surface could not reach. `release_audio` stays a direction on one
    function rather than becoming two, exactly as :func:`set_archived` and
    :func:`apply_tags` are one each: the bare form is the same operation with the
    deletion refused, and two copies would be two guards that could drift.
    """
    meeting, why = resolve_meeting(config, meeting_id)
    if meeting is None:
        return Outcome(False, why)

    if meeting.dir.parent == config.paths.meetings_dir:
        # Not a failure: this is the state the caller was asking for.
        return Outcome(True, f"{meeting.id} is already in {config.paths.meetings_dir}")

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
        return Outcome(
            False,
            f"{meeting.id} still holds {names} ({megabytes:.1f} MB), "
            f"and audio may never enter {config.paths.meetings_dir}.\n"
            f"pass --release-audio to delete "
            f"{'them' if len(kept) != 1 else 'it'} and promote anyway - the "
            f"transcript is all that would be left{retry}",
        )

    said = []
    if kept:
        # Imported inside the branch, as `rerun` imports its own module: this is
        # the one place a light verb reaches into the transcription module, and
        # it reaches for the two functions that own the rule rather than for a
        # copy of them. `transcribe`'s own imports are numpy and siblings, so
        # nothing here pulls in torch.
        from referat.transcribe import release_audio as delete_audio

        freed = delete_audio(meeting)
        if freed < 0:
            return Outcome(
                False,
                f"could not delete {meeting.id}'s audio; it stays in {meeting.dir.parent}",
            )
        said.append(f"deleted {', '.join(p.name for p in kept)} ({freed / 1e6:.1f} MB)")

    if meeting.status is MeetingStatus.GATE_FAILED:
        meeting.status = MeetingStatus.TRANSCRIBED
        meeting.save()

    # Aliased, because this module's own guarded function is called
    # `promote_meeting` too and this is the primitive underneath it — the same
    # relation `apply_name` has to `name_speaker`. The alias says which is which
    # at the one call site rather than leaving a reader to work it out.
    from referat.transcribe import promote_meeting as move_out_of_staging

    if not move_out_of_staging(meeting, config):
        # It never raises and logs why; a meeting that could not be moved is
        # still a finished meeting with a transcript in it.
        return Outcome(
            False,
            f"could not move {meeting.id} into {config.paths.meetings_dir}; see the log",
        )

    index.write_index(config)
    said.append(f"{meeting.id}: {meeting.status}, moved into {config.paths.meetings_dir}")
    return Outcome(True, "\n".join(said))


def run_promote(config: Config, meeting_id: str, *, release_audio: bool) -> int:
    """`referat promote <id> [--release-audio]`. The rule is :func:`promote_meeting`'s."""
    outcome = promote_meeting(config, meeting_id, release_audio=release_audio)
    return _report(outcome, "promote")


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
    reads `input()` and answers *no* on EOF, so a caller with no terminal cannot
    confirm without it. See the `--forget` flag it copies.
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

    print(f"{meeting.id} ({meeting.status}, {format_duration(meeting.duration_seconds)})")
    # The same warning the command center's modal shows, said once in
    # `delete_warning` so the two cannot drift into different accounts of an
    # irreversible act.
    for line in delete_warning(config, meeting).split("\n"):
        print(f"  {line}")

    if not assume_yes:
        try:
            answer = input(f"Delete {meeting.id}? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            print("Nothing was deleted.")
            return 0

    outcome = delete_meeting(config, meeting.id)
    return _report(outcome, "delete")


def delete_warning(config: Config, meeting: Meeting) -> str:
    """What somebody has to be told *before* a deletion, as one block of prose.

    Shown by the prompt above and by the command center's modal, which is the one
    place a UI says something Python also says — there is no outcome to quote
    yet, because the point is to speak before the command runs. Saying it here
    rather than in each surface is what keeps the two from drifting into
    different warnings about the same irreversible act.
    """
    staged = meeting.dir.parent != config.paths.meetings_dir
    kept = [p for p in (meeting.mic_path, meeting.system_path) if p.exists()]
    lines = [f"{meeting.dir}  -  {_folder_bytes(meeting.dir) / 1e6:.1f} MB"]
    if kept:
        lines.append(f"Audio still on disk: {', '.join(p.name for p in kept)}.")
    if staged:
        lines.append("In staging, outside any synced folder, so this deletion is real.")
    else:
        lines.append(
            "In the meetings folder. If that folder is synced, deleting a file does "
            "not delete it: Dropbox keeps deleted files and prior versions on its "
            "servers for weeks."
        )
    lines.append(
        "The voiceprints this meeting contributed stay in the known-voices database "
        "- `referat label --forget <name>` is how a person is removed."
    )
    return "\n".join(lines)


def delete_meeting(config: Config, meeting_id: str) -> Outcome:
    """Remove a meeting folder and everything in it. The only implementation.

    The refusal while a meeting is live is the rule this carries, and it is the
    reason a surface may not simply call :func:`referat.paths.remove_meeting_dir`:
    deleting a folder underneath the tray would lose audio mid-write.

    The **confirmation is not here**, exactly as it is not in
    :func:`referat.label.forget_person`. It is a question, asked of a terminal by
    `input()` and of a person by a modal, and :func:`delete_warning` is the one
    text both of them ask it with.
    """
    meeting, why = resolve_meeting(config, meeting_id)
    if meeting is None:
        return Outcome(False, why)

    if meeting.status in (MeetingStatus.RECORDING, MeetingStatus.TRANSCRIBING):
        return Outcome(
            False,
            f"{meeting.id} is {meeting.status} right now. Deleting it underneath the "
            f"tray would lose audio mid-write; wait for it to finish.",
        )

    megabytes = _folder_bytes(meeting.dir) / 1e6
    if not paths.remove_meeting_dir(meeting.dir):
        return Outcome(False, f"could not remove {meeting.dir}; see the log")

    index.write_index(config)
    return Outcome(True, f"deleted {meeting.id} ({megabytes:.1f} MB)")


# --- referat status ---------------------------------------------------------


def status_document() -> dict[str, Any]:
    """`referat status --json`: what the tray is doing, as a document.

    The fourth JSON document, after `list`, `label` and `project list`, and added
    for the same reason as the first three: a status bar renders what Python
    already worked out rather than parsing prose written for a person. In
    particular `elapsed` is :func:`referat.meeting.format_duration`'s output, so
    nothing outside Python ever grew a second duration formatter — the mistake
    this project has been pulled back from twice.

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


# --- referat notes ----------------------------------------------------------


def write_notes(config: Config, meeting_id: str) -> Outcome:
    """Run `/cleanup` and record the lifecycle. The only implementation.

    Two steps that belong together and are separately owned: spawning the pass is
    :func:`referat.notes.generate_notes`, and `notes_written` is
    :func:`set_notes_written`, because `/cleanup` is forbidden from touching
    `meta.json` and whoever spawned it says so afterwards. This is the *operation*
    over both, which is what `referat notes` and the command center's button each
    call once rather than sequencing for themselves.

    A pass that wrote the notes but could not record the state is reported as a
    partial success rather than a failure: `notes.md` is on disk and re-running
    would rewrite it, so calling that a failure would send somebody to fix the
    wrong thing. The lifecycle refusal is quoted as the reason.
    """
    from referat import notes

    written, message = notes.generate_notes(config, meeting_id)
    if not written:
        return Outcome(False, message)

    recorded = set_notes_written(config, meeting_id)
    if not recorded.ok:
        return Outcome(True, f"{message}, but the lifecycle was not updated: {recorded.message}")
    return Outcome(True, message)


def run_notes(config: Config, meeting_id: str) -> int:
    """`referat notes <id>`. Blocking, and it prints nothing until claude answers.

    The progress a window shows goes through :mod:`referat.progress`; at a prompt
    the same information is the log, which is where `claude`'s own output already
    goes line by line.
    """
    return _report(write_notes(config, meeting_id), "notes")


# --- referat people ---------------------------------------------------------


PEOPLE_HEADERS = ("NAME", "PRINTS", "FILED FROM", "APPEARS IN", "PROJECTS")
PEOPLE_RIGHT_ALIGNED = (1, 2, 3)

NO_PRINTS = (
    "no voiceprint on file, but a transcript still calls somebody this - a rerun "
    "that renumbered past them, or a hand edit. `referat label <id>` files one "
    "again, or `--forget` takes the name out of the transcripts too"
)

ONLY_ARCHIVED = (
    "every project they are tagged with has been archived. Derived on every read "
    "and stored nowhere - unarchive one and they are current again"
)


def _inactive(person: people_mod.Person, archived: set[str]) -> bool:
    """Every project they are tagged with is archived, and nothing is unaccounted for.

    **Three ways to be active, and all three are the same principle**: an unknown
    must never be read as an ending, because calling somebody finished when they
    are not is the error that costs something.

    Somebody with **no tags at all** is active — appearing only in untagged
    meetings means no project, and no project is not a finished one. An
    **orphaned** tag is not archived either, so a person whose only tag belongs to
    a deleted project stays active: that says the project record is gone, not that
    the work stopped. And somebody seen in an **untagged meeting** is active even
    when every tag they do carry is archived, which is why
    :attr:`referat.people.Person.in_untagged` exists — `tags` alone cannot tell
    that person from one seen only in the archived project, since an untagged
    meeting contributes no id to compare. That meeting is work nobody has
    labelled yet, and the untagged queue is a queue precisely because it gets
    drained later.

    Derived on every read and stored nowhere. There is no `inactive` key in any
    file and there must not be: it would be a fifth thing to keep in step with
    `meta.json`'s tags, the voices database and the transcripts, and the first
    one to disagree with them. It is computed here rather than in
    :func:`referat.people.directory` for a harder reason than tidiness — that
    module deliberately never opens `projects.json`, and `people.gallery` is
    built on it, so archivedness introduced there would reach the speaker
    dialog's scoping, which is the one place it must never go. `in_untagged` is
    the other half and lives *there* for the mirror-image reason: it is a fact
    about `meta.json` alone, so it costs that module no new file.
    """
    if person.in_untagged or not person.tags:
        return False
    return all(pid in archived for pid in person.tags)


def people_document(config: Config) -> dict[str, Any]:
    """`referat people --json`: everybody Referat knows a name for.

    The ninth JSON document, and the one that has to be read twice before it is
    changed. **Names, counts and meeting ids** — no embedding, no path into
    `.voices/`, not even the folder those live in. The database is biometric
    personal data about people who never asked to be in it, and a listing verb is
    the first place that is easy to forget; :func:`referat.people.directory` keeps
    the same promise one layer down, and this adds nothing to what it returns.

    The join is :mod:`referat.people`'s and is not re-derived here, exactly as
    :func:`hotwords_document` merges nothing itself. What this adds is the
    presentation join every other document already carries: `projects` is
    :meth:`referat.projects.ProjectsDB.name_map`, the very map
    :func:`list_document` and :func:`project_document` embed, so a tag and its
    display name cannot drift between the three.

    `owner` crosses as a string, as it does in
    :func:`referat.label.label_document`: what the owner is *called* is Python's
    to know, and whether that name leads a list is the surface's to decide.

    `archived` and each person's `inactive` are build step 21's, and they are
    both here rather than one of them: the flag says which section somebody
    belongs in, and the list is what lets the page mark *which* of their projects
    is the archived one — which is what explains the section. Neither is stored
    anywhere; see :func:`_inactive` for the three ways to be active.
    """
    from referat import people

    # One loader, two views. `archived` is exported beside the map as well as
    # folded into each person's `inactive`, because the page marks which of
    # somebody's projects are archived — which is what explains the section they
    # are sitting in — and because a document you can print is one you can test.
    db = projects.ProjectsDB.load(config)
    archived = db.archived_ids()
    return {
        "owner": config.speakers.owner_name.strip(),
        "projects": db.name_map(),
        "archived": sorted(archived),
        "people": [
            {
                "name": person.name,
                "prints": person.prints,
                "in_database": person.in_database,
                "is_owner": person.is_owner,
                "filed_from": [asdict(f) for f in person.filed_from],
                "appears_in": list(person.appears_in),
                "tags": list(person.tags),
                "inactive": _inactive(person, archived),
            }
            for person in people.directory(config)
        ],
    }


def run_people(config: Config, as_json: bool = False) -> int:
    """`referat people`. Two JSON reads and a folder scan, so it needs no extra."""
    document = people_document(config)
    if as_json:
        print(json.dumps(document, indent=2))
        return 0

    entries = document["people"]
    if not entries:
        print(
            "Nobody is known yet. A name is filed by `referat label <meeting-id>`, "
            "which plays an unnamed speaker and asks who it was."
        )
        return 0

    known = document["projects"]
    rows = [
        (
            p["name"] + (" (you)" if p["is_owner"] else ""),
            str(p["prints"]) if p["in_database"] else "-",
            str(len({f["meeting"] for f in p["filed_from"]})),
            str(len(p["appears_in"])),
            ", ".join(pid if pid in known else f"{pid}?" for pid in p["tags"]) or "-",
        )
        for p in entries
    ]
    print(render_table(rows, PEOPLE_HEADERS, PEOPLE_RIGHT_ALIGNED))
    print()
    print(f"{len(rows)} known name{'s' if len(rows) != 1 else ''}")

    # FILED FROM and APPEARS IN are two counts of two different things and the
    # table cannot say so in a header. The one that is worth spelling out is the
    # gap: a print records where it was *filed*, and somebody recognised
    # automatically files nothing new, so appearing in more meetings than you have
    # prints from is the normal state rather than a discrepancy.
    print(
        "FILED FROM counts the meetings a voiceprint came out of; APPEARS IN counts "
        "the meetings the name is used in. Recognition files nothing new, so the "
        "second is usually the larger."
    )
    drifted = [p["name"] for p in entries if not p["in_database"]]
    if drifted:
        # Named rather than counted, for the reason the hotword cap's drop list is:
        # this is the only place the two files are compared.
        print(f"{', '.join(drifted)}: {NO_PRINTS}")

    # A footer rather than a sixth column, and rather than a second marker in the
    # PROJECTS cell: that cell already has one vocabulary — a trailing `?` for an
    # orphan — and a second would blunt the one that means something.
    inactive = [p["name"] for p in entries if p["inactive"]]
    if inactive:
        print(f"{', '.join(inactive)}: {ONLY_ARCHIVED}")
    return 0


ACTION_HEADERS = ("KEY", "OWNER", "DUE", "MEETING", "ITEM")

EVERYONE = "*"
"""What `--person` takes to mean *do not filter at all*.

A sentinel rather than an empty string, because an empty `--person` is much more
likely to be a shell variable that did not expand than a request for everybody.
"""


def is_mine(item: dict[str, Any], owner: str) -> bool:
    """Whether an action item is the owner's, said once so two surfaces agree.

    `owner` is `[speakers].owner_name`. The comparison is exact against the names
    the note gave: a collective like `All authors` is **not** resolved to include
    the owner even where it plainly does, because working that out would be
    inferring an assignment, and that is the one line
    :mod:`referat.ui.dashboard` records this feature as not crossing.

    `Unassigned` is not mine either. The tab offers it as its own choice one
    click away, which is the honest arrangement: an item nobody owns is a
    question about the meeting, not an item on somebody's list.
    """
    return bool(owner) and owner in item["owners"]


def owner_counts(document: dict[str, Any]) -> list[tuple[str, int]]:
    """Every owner with a count, most items first, for the tab's person picker.

    Ties break alphabetically so the order is stable between refreshes — a picker
    whose entries swapped places when a meeting was transcribed would be a picker
    nobody could learn.
    """
    counts: dict[str, int] = {}
    for item in document["items"]:
        for name in item["owners"] or [actions.UNASSIGNED]:
            counts[name] = counts.get(name, 0) + 1
    return sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))


def day_summary_path(config: Config, day: str) -> Path:
    """`<meetings_dir>/days/<day>.md`, whether or not it is there yet."""
    return config.paths.meetings_dir / paths.DAYS_DIR / f"{day}.md"


def write_day_summary(config: Config, day: str) -> Outcome:
    """Write one day's summary by spawning `/standup`. The only implementation.

    `referat day` is this printed and the command center's button is this on the
    notes queue, which is the arrangement every write in this project has.

    **Thinner than :func:`write_notes`, and the reason is worth stating** so that
    nobody symmetrically adds the missing half. `write_notes` is an operation over
    *two* things because `/cleanup` is forbidden to touch `meta.json` and somebody
    has to record `notes_written` afterwards. A day is not a meeting: it has no
    `meta.json`, no lifecycle and no status anything could hold, so there is
    nothing to record and `referat state` must never learn a transition for it.
    """
    from referat import notes

    written, message = notes.generate_day_summary(config, day)
    return Outcome(written, message)


def run_day(config: Config, day: str = "") -> int:
    """`referat day [<YYYY-MM-DD>]`. Today when nothing is given."""
    return _report(
        write_day_summary(config, day or dt.date.today().isoformat()), "day"
    )


def actions_document(
    config: Config, document: dict[str, Any] | None = None
) -> dict[str, Any]:
    """`referat actions --json`: every action item in every meeting, and its state.

    **Pure over a `list_document` when it is handed one**, exactly as
    :func:`pending` is, and for a sharper reason. Every other document builder
    loads the meetings for itself; if this one did, the command center would pay
    a *second* scan of both meeting roots on every refresh, having just paid for
    the first. Handed the listing it already holds, an extra tab and an extra
    dashboard box together cost the notes files and nothing else.

    Reading `notes.md` is what this does and it is allowed to: the note is a
    *derived* artifact, so parsing it does not breach *nothing is inferred from a
    transcript*. What it may never do is act on what it reads — nothing here tags
    a meeting, proposes a tag, or reorders a queue.

    `complaint` carries an unreadable `actions.json` for the reason
    :func:`project_document` carries an unreadable projects file, and here it
    matters more than usual: with no state every item reads as **open**, so a
    broken file makes the dashboard's box longer at precisely the moment it is
    least trustworthy. Every surface shows the sentence instead.

    `orphans` are stored ticks whose item no longer appears in any note, which is
    what a `/cleanup` re-run leaves behind when it re-words a bullet. They are
    reported and never pruned on read — pruning here would make a read into a
    write, which is the one thing these loaders refuse to be.
    """
    if document is None:
        document = list_document(config)
    db = actions.ActionsDB.load(config)
    known = projects.ProjectsDB.load(config).name_map()

    items: list[dict[str, Any]] = []
    live: set[tuple[str, str]] = set()
    for meeting in document["meetings"]:
        folder = Path(meeting["dir"])
        for item in actions.read_actions(folder / paths.NOTES_MD, meeting["id"]):
            state = db.get(meeting["id"], item.key)
            live.add((meeting["id"], item.key))
            items.append(
                {
                    "key": item.key,
                    "meeting": meeting["id"],
                    "meeting_title": meeting["title"],
                    "started_at": meeting["started_at"],
                    "tags": list(meeting["tags"]),
                    "owners": list(item.owners),
                    "owner_text": item.owner_text(),
                    "unassigned": item.unassigned,
                    "qualifier": item.qualifier,
                    "text": state.text or item.text,
                    "original": item.text,
                    "edited": bool(state.text),
                    "due": item.due,
                    "at": list(item.at),
                    "done": state.done,
                    "done_at": state.done_at,
                    "dismissed": state.dismissed,
                }
            )

    orphans = [
        {"meeting": mid, "key": key, "seen": db.get(mid, key).seen}
        for mid, key in sorted(db.known() - live)
    ]
    owner = config.speakers.owner_name.strip()
    open_items = [i for i in items if not i["done"] and not i["dismissed"]]
    return {
        "owner": owner,
        "projects": known,
        "complaint": _unreadable_complaint(db.path, CANNOT_TICK) if db.unreadable else "",
        "counts": {
            "total": len(items),
            "open": len(open_items),
            "mine": sum(1 for i in open_items if is_mine(i, owner)),
            "done": sum(1 for i in items if i["done"]),
            "orphans": len(orphans),
        },
        "items": items,
        "orphans": orphans,
    }


def action_sort_key(item: dict[str, Any]) -> tuple[int, str, str]:
    """Soonest deadline first, undated last, then oldest meeting.

    Undated goes last rather than first because a date is a claim and its absence
    is not: `no date` items are the long tail of every one of these lists, and
    putting them on top would bury the three things that are actually due.
    """
    return (0, item["due"], item["started_at"]) if item["due"] else (1, "", item["started_at"])


def select_actions(
    document: dict[str, Any],
    person: str = "",
    include_done: bool = False,
    meeting: str = "",
) -> list[dict[str, Any]]:
    """The items one surface is showing, filtered and ordered. Pure.

    `person` is a name, :data:`EVERYONE`, :data:`referat.actions.UNASSIGNED`, or
    empty for the owner's own. Shared by the CLI, the tab and the clipboard so
    that what is copied is exactly what is shown — a Copy button that quietly
    exported a different set than the one on screen would be worse than no Copy
    button.
    """
    owner = document["owner"]
    wanted = person or owner
    items = [i for i in document["items"] if not i["dismissed"]]
    if not include_done:
        items = [i for i in items if not i["done"]]
    if meeting:
        items = [i for i in items if i["meeting"] == meeting]
    if wanted == actions.UNASSIGNED:
        items = [i for i in items if i["unassigned"] or not i["owners"]]
    elif wanted != EVERYONE:
        items = [i for i in items if wanted in i["owners"]]
    return sorted(items, key=action_sort_key)


def actions_markdown(items: Sequence[dict[str, Any]]) -> str:
    """The clipboard payload, and `referat actions --markdown`'s output.

    One implementation, because the whole point of the Copy button is pasting
    into somebody's own TODO list and a CLI that produced a different shape would
    make the two impossible to compare. A `- [ ]` task list, which is what every
    Markdown TODO understands, carrying the due date and the meeting it came from
    and **not** the timestamps — those are citations into a transcript that only
    exists on this machine, so they are noise anywhere this text is going.

    The annotation is parenthetical and deliberately **plain ASCII**, where the
    obvious choice was the em dash the notes themselves use. This string is the
    one thing here that leaves Referat — onto a clipboard, or down a pipe into
    a file — and this console's code page already renders an em dash as a
    replacement character, which is how the choice got noticed. Adding an
    encoding hazard to the one payload whose whole purpose is to be pasted
    somewhere else is the wrong place to be typographically nice. Whatever the
    note's own wording contains is passed through untouched.
    """
    lines: list[str] = []
    for item in items:
        note = [f"due {item['due']}"] if item["due"] else []
        note.append(item["meeting"])
        box = "x" if item["done"] else " "
        lines.append(f"- [{box}] {item['text'].rstrip('.')} ({', '.join(note)})")
    return "\n".join(lines) + ("\n" if lines else "")


def _open_actions(config: Config) -> tuple[actions.ActionsDB | None, str]:
    """Load the action store for a write, refusing an unreadable one.

    The counterpart of :func:`_open_project`, minus the second guard: there is no
    id to be unknown here, because a key is resolved against the notes rather
    than against this file. :data:`WOULD_OVERWRITE` because the caller is about
    to save.
    """
    db = actions.ActionsDB.load(config)
    if db.unreadable:
        return None, _unreadable_complaint(db.path, WOULD_OVERWRITE)
    return db, ""


def resolve_action(
    config: Config, key: str, document: dict[str, Any] | None = None
) -> tuple[dict[str, Any] | None, str]:
    """One item by key or by a unique prefix of one, or a complaint.

    Prefixes because a twelve-character hash is not something anybody types in
    full, and the same courtesy `git` extends. An **ambiguous** prefix is refused
    by naming every key it matched rather than by silently taking the first: the
    two candidates are different items in different meetings, and acting on the
    wrong one would tick something nobody looked at.
    """
    if document is None:
        document = actions_document(config)
    matches = [i for i in document["items"] if i["key"].startswith(key)]
    if not matches:
        return None, f"no action item {key}; `referat actions` lists them with their keys"
    if len(matches) > 1:
        found = ", ".join(sorted(i["key"] for i in matches))
        return None, f"{key} matches {len(matches)} items ({found}); use more of the key"
    return matches[0], ""


def _write_action(
    config: Config, key: str, change: Callable[[actions.Entry, dict[str, Any]], str]
) -> Outcome:
    """Resolve one item, apply one change to its entry, save once.

    Every action mutation is this shape, so the guard order is stated once: the
    unreadable file before the lookup, because a complaint about an unknown key
    is advice about the wrong problem when the file that records keys did not
    parse. `change` returns the sentence to report.

    `seen` is stamped on the way through. It is the note's own wording at the
    moment somebody acted on the item, and it is the only thing that will make
    this entry legible later if `/cleanup` re-words the bullet out from under it.
    """
    db, why = _open_actions(config)
    if db is None:
        return Outcome(False, why)
    item, complaint = resolve_action(config, key)
    if item is None:
        return Outcome(False, complaint)
    entry = db.entry(item["meeting"], item["key"])
    entry.seen = item["original"]
    message = change(entry, item)
    db.save()
    return Outcome(True, message)


def set_action_done(config: Config, key: str, done: bool = True) -> Outcome:
    """Tick an action item off, or put it back."""

    def change(entry: actions.Entry, item: dict[str, Any]) -> str:
        entry.done = done
        entry.done_at = dt.datetime.now().isoformat(timespec="seconds") if done else ""
        return f"{item['key']}: {'done' if done else 'not done'} - {item['text'][:60]}"

    return _write_action(config, key, change)


def dismiss_action(config: Config, key: str, dismissed: bool = True) -> Outcome:
    """Drop an action item from the lists, or bring it back.

    **This does not touch `notes.md`.** The note is the record of what the meeting
    said, and an item somebody drops because it was never really an action item
    is still a thing the meeting produced. Dropping it says *do not show me this*,
    which is a fact about the reader, and facts about the reader live in
    `actions.json`. `referat actions restore` is the way back, and a re-read of
    the note is the other one.
    """

    def change(entry: actions.Entry, item: dict[str, Any]) -> str:
        entry.dismissed = dismissed
        verb = "dropped" if dismissed else "restored"
        return f"{item['key']}: {verb} - {item['text'][:60]}"

    return _write_action(config, key, change)


def edit_action(config: Config, key: str, text: str) -> Outcome:
    """Correct an item's wording, or revert to the note's when `text` is empty.

    The correction is stored beside the item and the **key does not move**,
    because a key is a hash of what the *note* says and never of what was typed
    over it. That is what makes an edit revertible: the original is still on disk,
    in the file this never writes.
    """

    def change(entry: actions.Entry, item: dict[str, Any]) -> str:
        wanted = " ".join(text.split())
        entry.text = "" if wanted == item["original"] else wanted
        if not entry.text:
            return f"{item['key']}: back to what the notes say - {item['original'][:60]}"
        return f"{item['key']}: {entry.text[:60]}"

    return _write_action(config, key, change)


def prune_actions(config: Config, keys: Sequence[str] | None = None) -> Outcome:
    """Forget stored state whose action item is no longer in any note.

    Only ever on request. An orphan is a tick whose bullet `/cleanup` re-worded,
    and it is kept and shown rather than cleaned up quietly for the reason a
    deleted project's tags are rendered as orphans: state that vanishes on its own
    is state nobody can trust. This is the button that says yes.
    """
    db, why = _open_actions(config)
    if db is None:
        return Outcome(False, why)
    orphans = actions_document(config)["orphans"]
    wanted = set(keys) if keys else None
    gone = 0
    for orphan in orphans:
        if wanted is not None and orphan["key"] not in wanted:
            continue
        gone += db.drop(orphan["meeting"], orphan["key"])
    if not gone:
        return Outcome(True, "nothing to prune")
    db.save()
    return Outcome(True, f"forgot {gone} orphaned item{'s' if gone != 1 else ''}")


def run_actions(config: Config, args: argparse.Namespace) -> int:
    """`referat actions`, and the five verbs under it.

    No verb means `list`, unlike `referat project`, and the difference is real:
    `project` is a namespace of things you do to projects, while `actions` is a
    **question** — the answer to which is the list. Making somebody type
    `actions list` to ask it would be a namespace pretending to be a verb.
    """
    verb = getattr(args, "actions_command", None) or "list"
    if verb == "done":
        return _report(set_action_done(config, args.key, True), "actions")
    if verb == "undone":
        return _report(set_action_done(config, args.key, False), "actions")
    if verb == "drop":
        return _report(dismiss_action(config, args.key, True), "actions")
    if verb == "restore":
        return _report(dismiss_action(config, args.key, False), "actions")
    if verb == "edit":
        return _report(edit_action(config, args.key, " ".join(args.text)), "actions")
    if verb == "prune":
        return _report(prune_actions(config, args.key or None), "actions")

    document = actions_document(config)
    if getattr(args, "as_json", False):
        print(json.dumps(document, indent=2))
        return 0

    person = EVERYONE if getattr(args, "everyone", False) else getattr(args, "person", "") or ""
    items = select_actions(
        document, person, include_done=args.done, meeting=getattr(args, "meeting", "") or ""
    )
    if getattr(args, "as_markdown", False):
        print(actions_markdown(items), end="")
        return 0

    if document["complaint"]:
        print(f"referat actions: {document['complaint']}", file=sys.stderr)

    whose = person or document["owner"]
    if not items:
        if not document["items"]:
            print(
                "No action items yet. They are the `## Action items` section of a "
                "meeting's notes.md, written by `referat notes <id>`."
            )
        else:
            print(f"Nothing open for {whose}. Try --everyone, or --done to see what is finished.")
        return 0

    rows = [
        (
            item["key"][:8],
            item["owner_text"][:22],
            item["due"] or "-",
            item["meeting"],
            (("[x] " if item["done"] else "") + item["text"])[:72],
        )
        for item in items
    ]
    print(render_table(rows, ACTION_HEADERS))
    print()
    counts = document["counts"]
    print(
        f"{len(rows)} shown for {'everybody' if whose == EVERYONE else whose}; "
        f"{counts['open']} open in all, {counts['mine']} of them {document['owner']}'s"
    )
    if counts["orphans"]:
        # Named as a count rather than listed, because the list is only useful
        # next to what it used to say - which is what `actions prune` prints.
        print(
            f"{counts['orphans']} stored item{'s' if counts['orphans'] != 1 else ''} no longer "
            f"{'appear' if counts['orphans'] != 1 else 'appears'} in any notes.md; a /cleanup "
            f"re-run re-worded {'them' if counts['orphans'] != 1 else 'it'}. "
            f"`referat actions prune` forgets {'them' if counts['orphans'] != 1 else 'it'}."
        )
    return 0


HOTWORD_HEADERS = ("TERM", "SOURCE")

HOTWORD_SOURCES = (
    "[transcription].hotword_extras in config.toml",
    "a name in the known-voices database (referat label)",
    "a project's glossary (referat project glossary <id>)",
)
"""The three places a term reaches this list from, in the priority order the cap uses.

Said in one place because two surfaces say it: the empty-list message here, and
the command center's hotword panel, which is the screen somebody will be *adding*
terms on when they push the list over the budget.
"""


def hotwords_document(config: Config) -> dict[str, Any]:
    """The merged hotword list, what it cost, and what the cap dropped.

    Split out of :func:`run_hotwords` at build step 20's phase 4, and the `--json`
    that step 12b deliberately withheld comes with it. That refusal was recorded
    with its reason — nothing read this document — and phase 4 is where that stops
    being true: the command center's projects page is the hotword surface, because
    a glossary is where the terms live.

    The token figures are :func:`referat.hotwords.estimate_tokens`' estimate rather
    than a count, and are named `estimated_tokens` so no reader mistakes them for
    one. There is no tokenizer to ask without the `transcribe` extra, and the
    estimate errs upwards on purpose so that a term is dropped here and named
    rather than sliced mid-name by faster-whisper and not.
    """
    from referat import hotwords

    kept, dropped = hotwords.cap(hotwords.collect(config))
    return {
        "budget": hotwords.TOKEN_BUDGET,
        "estimated_tokens": sum(hotwords.estimate_tokens(t.term) for t in kept),
        "terms": [{"term": t.term, "source": t.source} for t in kept],
        "dropped": [{"term": t.term, "source": t.source} for t in dropped],
    }


def run_hotwords(config: Config, as_json: bool = False) -> int:
    """`referat hotwords` -- the list, where each term came from, and what was cut.

    Two JSON reads, so it needs no optional extra -- naming what Whisper will be
    told should not cost three gigabytes of resident torch.
    """
    document = hotwords_document(config)
    if as_json:
        print(json.dumps(document, indent=2))
        return 0

    terms = document["terms"]
    dropped = document["dropped"]
    if not terms and not dropped:
        print("No hotwords. A term reaches this list from one of three places:")
        for source in HOTWORD_SOURCES:
            print(f"  {source}")
        return 0

    print(render_table([(t["term"], t["source"]) for t in terms], HOTWORD_HEADERS))
    print()
    print(
        f"{len(terms)} term{'s' if len(terms) != 1 else ''}, "
        f"about {document['estimated_tokens']} of {document['budget']} tokens "
        f"of Whisper's prompt window"
    )
    if dropped:
        # Named rather than counted. A cap nobody can see is how this turns into a
        # bug report about one specific name that is never heard right.
        print(
            f"{len(dropped)} dropped by the cap, lowest priority first: "
            + ", ".join(f"{t['term']} ({t['source']})" for t in dropped)
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
        return run_hotwords(config, as_json=args.as_json)

    if args.command == "day":
        return run_day(config, args.day)

    if args.command == "actions":
        return run_actions(config, args)

    if args.command == "people":
        return run_people(config, as_json=args.as_json)

    if args.command == "notes":
        return run_notes(config, args.meeting_id)

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
