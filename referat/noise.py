"""Marking a diarized cluster as *not a person*, and taking its lines out.

Build step 20b. Diarization clusters whatever it hears, and on a room microphone
that includes the corridor, the door and the meeting next door: `2026-09-03` has
a `SPEAKER_NN` that is nobody, and until this module the only way to stop the
*Speakers nobody has named* queue asking about it was to give it a name — which
files a voiceprint of a door. This is the third answer beside *name* and *leave*.

**A person marks it and the machine never infers it.** :mod:`referat.bleed`
decides by evidence — coverage in time and text against the other channel — and
refuses when the evidence is thin. There is no evidence here: nothing in the data
distinguishes a quiet neighbour from a quiet participant, and *that cluster is
the room next door* is a judgement made by listening to the snippets. So there is
no heuristic in this module, no "clusters under N seconds", and there must never
be one; the cost of being wrong is deleting somebody's actual words.

**It deletes lines from a transcript, which is the thing this project is most
careful about**, and it inherits `referat debleed`'s four conditions rather than
re-arguing them. Every removed entry is recorded in `meta.json` **before** the
transcript is touched, with the text kept verbatim, so an interruption leaves a
record of a removal that did not happen rather than a removal with no record.
The record **accumulates** across passes rather than being replaced. The CLI
verb is dry by default and `--apply` acts. And the removal stays checkable:
somebody can read `transcription.noise` and see exactly what went, and why —
which here is always the same reason, that a person said so.

**Two flags rather than one.** :attr:`referat.voices.Cluster.echo` means
something specific and true — *this is the loopback coming back into the
microphone* — and was reached by measurement. `noise` means *a person listened
and said this is not a person*. A later reader has to be able to tell which
judgement was made, so the second verdict sits beside the first and neither is
widened to cover the other. What they share is the defence: a noise cluster is
never offered a name, by the same two functions that refuse an echo one.

**A cluster somebody has already named is refused.** That is a person a human
recognised, and marking them noise would delete the words of somebody who was
identified. `referat label --forget <name>` is the way back from a wrong name,
and it comes first.

**Nothing here touches the voices database**, which is the difference between
this and every other thing the Speakers dialog does: a noise cluster has no
voiceprint to file and its embedding is left in `meta.json` where it is inert,
exactly as an echo cluster's is. The snippets go, by the argument that keeps echo
snippets from being offered — `speakers/SPEAKER_NN_1.wav` left on disk is an
invitation to name it by hand.

Per meeting, always: `SPEAKER_NN` numbering is per meeting, so dismissing *a
number* globally would silence a different person next week.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

from referat import index, paths, voices
from referat.config import Config
from referat.meeting import Meeting, MeetingStatus, resolve_meeting

log = logging.getLogger(__name__)

NOISE_KEY = "noise"
"""Where the record lives: `transcription.noise`, beside `transcription.debleed`.

Keyed by label, because one meeting can have several noise clusters and each is
a separate judgement made at a separate time. Each entry is `{at, channel,
removed: [{at, text}, ...]}`, and `removed` **accumulates**: a second pass after
an interrupted first one appends what it finally removed rather than replacing
the record of what the first pass meant to.
"""

CHANNELS = {"mic": "your microphone", "system": "the call"}
"""How a channel is said to a person — the same two phrases `referat label` uses."""


@dataclass(frozen=True)
class Plan:
    """What marking one cluster as noise would do, decided without doing it.

    The verdict split from the act, the way :func:`referat.bleed.suppress` is
    from :func:`referat.bleed.apply`: this is what the dry run prints, what the
    dialog's modal quotes, and what :func:`mark_noise` then carries out.
    """

    speaker: str
    channel: str
    entries: list[tuple[int, str, str]] = field(default_factory=list)
    """`(line index, HH:MM:SS, text)` for every line of the transcript under this label."""
    snippets: int = 0
    """How many snippet files are still on disk for it."""
    already: bool = False
    """Whether an earlier pass already marked it — see :func:`mark_noise` on convergence."""

    @property
    def span(self) -> str:
        if not self.entries:
            return ""
        first, last = self.entries[0][1], self.entries[-1][1]
        return first if first == last else f"{first} to {last}"


# --- Deciding --------------------------------------------------------------


def complaint(config: Config, meeting: Meeting, speaker: str) -> str:
    """Why this cluster may not be marked as noise, or `""` when it may.

    Unprefixed, the way :func:`referat.meeting.resolve_meeting` returns its
    complaint, because the CLI says `referat denoise:` and a dialog says nothing.

    A cluster already marked is **not** refused here: the transcript write can
    have been interrupted after the record was written, and a second pass has to
    be able to finish the job. :func:`mark_noise` says *already marked* when it
    finds nothing left to do.
    """
    if meeting.status in (MeetingStatus.RECORDING, MeetingStatus.TRANSCRIBING):
        return (
            f"{meeting.id} is {meeting.status} right now, and the transcript is about "
            f"to be written over; wait for it to finish"
        )
    if not voices.speaker_channel(meeting, speaker):
        return f"{speaker} is not a diarized speaker of {meeting.id}"
    if value := meeting.speaker_names.get(speaker):
        # Rendered through the database: the stored value is an id, or the bare
        # name a file written before build step 20c stored, and the sentence
        # names the person as a reader knows them and the id `--forget` takes.
        db = voices.VoicesDB.load(config)
        return (
            f"{speaker} is {db.display(value)}, whom somebody identified, and marking a "
            f"named speaker as noise would delete the words of a person. If that name "
            f"was wrong, `referat label --forget {voices.person_id(value)}` comes first"
        )
    if voices.is_echo(meeting, speaker):
        return (
            f"{speaker} is the loopback coming back into the microphone; its lines "
            f"are already out of the transcript and it is already never offered a name"
        )
    return ""


def plan(config: Config, meeting: Meeting, speaker: str) -> tuple[Plan | None, str]:
    """Everything the removal would touch, or the complaint that stops it.

    Reads the transcript and nothing else. The lines are found by
    :func:`referat.transcribe.parse_entry`, the one parser of the format, and by
    label alone — a rendered line does not say which channel it came from, but a
    `SPEAKER_NN` is unique across the meeting (see
    :func:`referat.diarize.assign`), so the label is provenance enough.
    """
    # Local for the reason `cli.run_debleed` imports it locally: this module has
    # to be cheap to import from the dialog and the CLI alike.
    from referat.transcribe import format_timestamp, parse_entry

    why = complaint(config, meeting, speaker)
    if why:
        return None, why
    try:
        text = meeting.transcript_path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"could not read {meeting.transcript_path}: {exc}"

    entries: list[tuple[int, str, str]] = []
    for i, line in enumerate(text.split("\n")):
        parsed = parse_entry(line)
        if parsed is not None and parsed[1] == speaker:
            seconds, _label, said = parsed
            entries.append((i, format_timestamp(seconds), said))
    return (
        Plan(
            speaker=speaker,
            channel=voices.speaker_channel(meeting, speaker),
            entries=entries,
            snippets=len(voices.snippet_paths(meeting, speaker)),
            already=voices.is_noise(meeting, speaker),
        ),
        "",
    )


def warning(meeting: Meeting, plan: Plan) -> str:
    """What somebody is told *before* acting, as one block of prose.

    Said once here and quoted by the dry run and by the dialog's modal, for the
    reason :func:`referat.cli.delete_warning` exists: two surfaces describing
    the same irreversible act in two ways is how one of them ends up wrong.
    """
    where = CHANNELS.get(plan.channel, "")
    who = f"{plan.speaker} (from {where})" if where else plan.speaker
    lines: list[str] = []
    if plan.entries:
        span = f", {plan.span}" if plan.span else ""
        lines.append(
            f"{who}: {len(plan.entries)} line(s){span} would be removed from "
            f"{meeting.transcript_path.name}."
        )
    else:
        lines.append(f"{who}: no line of {meeting.transcript_path.name} carries this label.")
    if plan.already:
        lines.append("An earlier pass already marked this cluster as noise.")
    lines.append(
        "Every removed line is recorded in meta.json first, so the removal can be "
        "read back. This cluster is never offered a name again, and no voiceprint "
        "is filed."
    )
    if plan.snippets:
        lines.append(f"Its {plan.snippets} snippet(s) are deleted.")
    lines.append(
        "This is a judgement made by listening: nothing in the data tells a quiet "
        "neighbour from a quiet participant, so nothing checks it for you."
    )
    return "\n".join(lines)


# --- Acting ------------------------------------------------------------------


def mark_noise(config: Config, meeting_id: str, speaker: str) -> tuple[bool, str]:
    """Mark one cluster as noise and take its lines out. The only implementation.

    `referat denoise <id> --speaker <s> --apply` is this printed, and the Speakers
    dialog's third button is this in process — the arrangement
    :func:`referat.label.name_speaker` has over :func:`referat.label.apply_name`,
    and for the same reason: every rule is here, and a surface growing its own
    copy would be the second implementation.

    **Order of writes.** `meta.json` first, carrying the `noise` flag on the
    cluster and the full text of every line about to go; then the transcript;
    then the snippets; then the dashboard. The metadata is written through
    :func:`referat.paths.write_json_atomic` directly rather than
    :meth:`referat.meeting.Meeting.save`, which never raises — that is right for
    a recording, where the audio is the part that cannot be reconstructed, and
    wrong here, where the record *is* the justification for the deletion. A
    record that failed to land refuses the deletion outright.

    **Convergent rather than idempotent**, like `debleed`. An interruption between
    the two writes leaves a cluster flagged with its lines still in the file, and
    a second pass removes them and appends to the record. A cluster already marked
    with nothing left to remove is reported as such and nothing is written.

    Returns the value-and-unprefixed-complaint pair the rest of this codebase
    returns, because the prefix is the caller's.
    """
    meeting, why = resolve_meeting(config, meeting_id)
    if meeting is None:
        return False, why
    decided, why = plan(config, meeting, speaker)
    if decided is None:
        return False, why
    if decided.already and not decided.entries:
        return True, f"{speaker} is already marked as noise; nothing left to remove"

    stamp = dt.datetime.now().isoformat(timespec="seconds")
    record = dict(meeting.transcription.get(NOISE_KEY) or {})
    earlier = record.get(speaker) if isinstance(record.get(speaker), dict) else {}
    previous = earlier.get("removed") if isinstance(earlier, dict) else None
    record[speaker] = {
        "at": stamp,
        "channel": decided.channel,
        # **Appended, never replaced** — see `cli.run_debleed` for the pass that
        # taught this: a replaced record is a deletion nobody can read back.
        "removed": (previous if isinstance(previous, list) else [])
        + [{"at": at, "text": text} for _i, at, text in decided.entries],
    }
    if not voices.mark_stored_noise(meeting, speaker):
        # `complaint` already checked the cluster exists, so this is a file that
        # changed under us; say so rather than write a record about nothing.
        return False, f"{speaker} is no longer a diarized speaker of {meeting.id}"
    meeting.transcription = {**meeting.transcription, NOISE_KEY: record}
    try:
        paths.write_json_atomic(meeting.meta_path, meeting.to_json())
    except OSError as exc:
        return False, f"could not record the removal in {meeting.meta_path}: {exc}"

    if decided.entries:
        try:
            text = meeting.transcript_path.read_text(encoding="utf-8")
        except OSError as exc:
            return False, f"could not read {meeting.transcript_path}: {exc}"
        kept = _without(text.split("\n"), {i for i, _at, _text in decided.entries})
        try:
            paths.write_text_atomic(meeting.transcript_path, "\n".join(kept))
        except OSError as exc:
            # The record says these lines were removed and the file still holds
            # them. That is the harmless direction, and the next pass converges.
            return False, f"recorded in meta.json but could not write {meeting.transcript_path}: {exc}"

    dropped = voices.drop_snippets(meeting, speaker)
    # A noise cluster leaves `unknown_speakers`, so the Unnamed column moves —
    # every entry point that changes it regenerates the dashboard.
    index.write_index(config)
    log.info(
        "%s: %s is noise - %d line(s) removed, %d snippet(s) deleted",
        meeting.id,
        speaker,
        len(decided.entries),
        dropped,
    )
    return True, (
        f"{speaker} is noise: {len(decided.entries)} line(s) removed from "
        f"{meeting.transcript_path.name} and recorded in meta.json"
        + (f"; {dropped} snippet(s) deleted" if dropped else "")
    )


def _without(lines: list[str], doomed: set[int]) -> list[str]:
    """`lines` minus the doomed ones, each taking the blank line after it along.

    Entries are separated by a blank line (:data:`referat.transcribe.ENTRY_SEPARATOR`),
    so deleting an entry alone leaves two blanks in a row — harmless in Markdown,
    and `debleed` leaves them, but a file that reads as if the line was never
    there is the better record of a line that should never have been there.
    Every surviving line is byte-identical; nothing is re-rendered.
    """
    out: list[str] = []
    skip_blank = False
    for i, line in enumerate(lines):
        if i in doomed:
            skip_blank = True
            continue
        if skip_blank and line == "":
            skip_blank = False
            continue
        skip_blank = False
        out.append(line)
    return out
