"""How one row of `cli.list_document` is put into words.

Two pure formatters over a meeting *as the listing document describes it*, and
nothing else: no widget, no file, no `Meeting` object. They live here rather than
in :mod:`referat.ui.window` because phase 6 gave them a second reader — the
dashboard renders the same meetings in a different shape — and a status cell that
said one thing on the Meetings tab and another on the opening screen would be the
same drift this project keeps pulling `format_duration` and `audio_state` back
from, one layer up.

Both are the window's counterparts of functions in :mod:`referat.cli`, and both
differ from them in the same way and for the same reason: the CLI prints what you
are about to type and a window prints what you are about to read.
"""

from __future__ import annotations

from typing import Any


def status_text(meeting: dict[str, Any]) -> str:
    """The lifecycle, plus the two things that are not it and earn their place.

    A run that lost a channel says so here. `2026-09-02_1001` was twenty-four
    minutes with no microphone in it and read `transcribed`, which is true of the
    transcription and worthless as a description of the meeting. The same
    sentence :func:`referat.cli.list_row` makes.

    Staging is the second, and it is the one thing a person can act on: a meeting
    that is still there is a meeting whose audio is still on disk, either because
    the gate refused its transcript or because somebody asked to keep it.
    """
    lost = meeting["missing_channels"]
    text = str(meeting["status"]).replace("_", " ")
    if lost:
        text += f" - no {'/'.join(lost)}"
    if meeting["staged"]:
        text += " (staging)"
    return text


def tags_text(tags: list[str], known: dict[str, str]) -> str:
    """A meeting's tags as a person reads them: display names, orphans marked.

    The counterpart of :func:`referat.cli.tags_cell`, which prints *ids* because
    the next thing typed after reading that table is `referat untag <meeting>
    <id>`. Here there is nothing to type, so the names win — and an id no project
    resolves still gets its trailing `?` rather than being dropped, for the
    reason it does everywhere else: a tag disappearing quietly off three meetings
    is how you lose track of what a meeting was about.

    The map comes from the document, which got it from `ProjectsDB.name_map`.
    Nothing here opens `projects.json`.
    """
    return ", ".join(known.get(tag, f"{tag}?") for tag in tags)
