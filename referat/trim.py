"""Ending a meeting after the fact: everything past a point is discarded.

Build step 25. On 2026-09-10 a meeting ended at about 13:35, the laptop was
closed, and the recording ran on until somebody remembered it at 13:49. The
lid now stops a meeting (:class:`referat.power.PowerEvents`), which is the
prevention; this is the repair, and it is also the answer for the case no rule
catches — a meeting that ended while the laptop stayed open and nobody pressed
stop, with a corridor conversation, a phone call or a colleague's aside
recorded after it and filed as the meeting.

**The fifth repair, the third exception to the immutability rule, and it
differs from the other two on the one point that defines them.** `debleed`
and `denoise` keep every removed line verbatim in `meta.json`, so a deletion
can be read back. This one keeps the *cut* and never the content. The reason
to trim is that what follows the end of a meeting was never part of it and may
be things people said believing nothing was recording; a record that preserved
those lines under another key would be the deletion not happening. So
`transcription.trimmed` says when, where and how much — the cut as a
timestamp and as wall clock, how many lines, how many seconds of which WAV,
which snippets, which clusters — and a reader can verify the cut against the
file, since nothing after that timestamp remains, without being able to read
what went. That is the checkability the rule asks for, applied to a deletion
whose entire point is that it is not checkable by content.

**It reaches everything in the folder the discarded stretch touched.** The
transcript's entries at or after the cut and its header's duration; the WAVs,
truncated in place when they are still on disk (a `gate_failed` meeting, one
kept on request, or one not yet transcribed), because a later `rerun` would
otherwise bring the stretch straight back; the speaker snippets cut from after
it, which are audio of it; the unnamed clusters whose every line was after it,
which would otherwise sit in the *Speakers nobody has named* queue asking for
the name of somebody who was never in the meeting; the pauses after it, the
recorded duration and `ended_at`. What it does **not** reach is anything
derived from the untrimmed transcript that lives elsewhere: `notes.md`, the
digest block in a linked Google Doc, a day summary, the action items. It says
so, and drops a `notes_written` or `synced` meeting back to `transcribed` —
the state `rerun` writes for the same reason, and the one the dashboard's
notes queue already renders as *notes describe a transcript that no longer
exists* — so that regenerating the notes is the next thing offered, and the
auto-sync that follows a cleanup pass replaces the block.

**Named clusters are left alone, deliberately.** A cluster somebody
identified is a person, and the identification stays on record the way a
voiceprint filed from this meeting does; whether they spoke before or after
the cut is a fact about the transcript, which now says. Only the *unnamed*
clusters entirely after the cut are dropped, because leaving them would offer
a voice that is not in the meeting for naming. `label --forget` and
`--drop-voiceprint` are the tools for names.

**The cut is a person's, given as the transcript counts** — `HH:MM:SS` of
audio with pauses excluded, which is what the file shows and what somebody
reads to find *Great, thanks everyone* — or as a wall-clock time, which is how
the end of a meeting is remembered, converted through the pause list by
:func:`referat.meeting.wall_to_audio`. Nothing infers the point: an
end-of-meeting detector would be *nothing is inferred from a transcript*
broken at the one place a wrong guess deletes somebody's actual words.

Dry by default at the prompt, a modal in the window, `meta.json` first, and
convergent: an interrupted pass leaves a record of a cut and a file that still
runs past it, and the next pass at the same point finishes the job and appends
a second record. A pass at a *later* point than the meeting now ends at
changes nothing and writes nothing.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from referat import index, paths
from referat.config import Config
from referat.meeting import (
    Meeting,
    MeetingStatus,
    audio_to_wall,
    format_duration,
    resolve_meeting,
    wall_to_audio,
)

log = logging.getLogger(__name__)

TRIM_KEY = "trimmed"
"""Where the record lives: `transcription.trimmed`, a **list** of passes, oldest first.

A list rather than the dict `debleed` and `noise` keep, because a meeting can
legitimately be cut twice at two different points — once too generously, then
again — and each cut is its own decision made at its own time. No pass holds
any removed text; see the module docstring for why that is the point.
"""

COPY_FRAMES = 1 << 16
"""How many frames :func:`_truncate_wav` copies at a time."""


@dataclass(frozen=True)
class Plan:
    """What cutting one meeting at one point would do, decided without doing it.

    The verdict split from the act, as :class:`referat.noise.Plan` is: the dry
    run prints this, the window's modal quotes :func:`warning` over it, and
    :func:`trim` carries it out.
    """

    at: float
    """The cut, in seconds of audio with pauses excluded — the transcript's axis."""
    wall: float
    """The same instant in wall-clock seconds since the meeting started."""
    duration_before: float
    entries: list[tuple[int, str, str, str]] = field(default_factory=list)
    """`(line index, HH:MM:SS, label, text)` for every transcript line at or after the cut."""
    header: tuple[int, str] | None = None
    """The header line's index and its replacement, when the duration it states changes."""
    audio: dict[str, tuple[int, int]] = field(default_factory=dict)
    """Channel name to `(frames now, frames after)` for every WAV still on disk that runs past the cut."""
    snippets: list[Path] = field(default_factory=list)
    """Snippet files cut from after the point."""
    speakers_gone: list[str] = field(default_factory=list)
    """Unnamed clusters whose every transcript line is after the cut."""
    pauses_dropped: int = 0
    notes: bool = False
    """Whether a `notes.md` exists, and so may describe what is being discarded."""
    docs: int = 0
    """How many Google Docs hold a digest block written from the untrimmed notes."""

    @property
    def nothing(self) -> bool:
        """True when the meeting already ends at or before the cut."""
        return not (
            self.entries
            or self.header
            or self.audio
            or self.snippets
            or self.speakers_gone
            or self.pauses_dropped
            or self.wall < self.duration_before - 0.5
        )

    @property
    def span(self) -> str:
        if not self.entries:
            return ""
        first, last = self.entries[0][1], self.entries[-1][1]
        return first if first == last else f"{first} to {last}"


# --- Reading a cut ------------------------------------------------------------


def parse_timestamp(text: str) -> float | None:
    """`HH:MM:SS`, `H:MM:SS` or `MM:SS` into seconds, or None for anything else."""
    parts = text.strip().split(":")
    if len(parts) not in (2, 3) or not all(p.isdigit() for p in parts):
        return None
    numbers = [int(p) for p in parts]
    if len(numbers) == 2:
        numbers = [0, *numbers]
    hours, minutes, seconds = numbers
    if minutes > 59 or seconds > 59:
        return None
    return float(hours * 3600 + minutes * 60 + seconds)


def clock_to_audio(meeting: Meeting, text: str) -> tuple[float | None, str]:
    """A wall-clock `HH:MM[:SS]` on the meeting's day into the transcript's seconds.

    Returns the audio-elapsed seconds and `""`, or None and the complaint. A
    time earlier than the start is read as the next day only when the meeting
    could have run into it, which keeps `--clock 09:00` on a meeting that
    started at 13:15 a refusal rather than a cut twenty hours in.
    """
    try:
        when = dt.datetime.strptime(text.strip(), "%H:%M:%S").time()
    except ValueError:
        try:
            when = dt.datetime.strptime(text.strip(), "%H:%M").time()
        except ValueError:
            return None, f"{text!r} is not a time of day (HH:MM or HH:MM:SS)"
    instant = dt.datetime.combine(meeting.started_at.date(), when)
    wall = (instant - meeting.started_at).total_seconds()
    if wall < 0 and wall + 86400 <= meeting.duration_seconds:
        wall += 86400
    if wall < 0:
        return None, (
            f"{text} is before the meeting started at "
            f"{meeting.started_at.strftime('%H:%M:%S')}"
        )
    return wall_to_audio(meeting.pauses, wall), ""


def clock_of(meeting: Meeting, wall: float) -> str:
    """The wall-clock time `wall` seconds into the meeting, as `HH:MM:SS`."""
    return (meeting.started_at + dt.timedelta(seconds=wall)).strftime("%H:%M:%S")


# --- Deciding -----------------------------------------------------------------


def complaint(meeting: Meeting, at: float) -> str:
    """Why this meeting may not be cut here, or `""` when it may. Unprefixed."""
    if meeting.status in (MeetingStatus.RECORDING, MeetingStatus.TRANSCRIBING):
        return (
            f"{meeting.id} is {meeting.status} right now; stop it, or wait for the "
            f"transcript, and trim afterwards"
        )
    if at <= 0:
        return "the cut must be after the start of the meeting"
    return ""


def plan(config: Config, meeting: Meeting, at: float) -> tuple[Plan | None, str]:
    """Everything a cut at `at` seconds would touch, or the complaint that stops it.

    Reads the transcript, the WAV headers and the snippet folder, and writes
    nothing. The WAV lengths are read off the files rather than out of
    `meta.json`, so an interrupted pass — record written, file not yet cut —
    is finished by the next one.
    """
    from referat.transcribe import HEADER_RE, format_timestamp, parse_entry, render_header

    why = complaint(meeting, at)
    if why:
        return None, why
    wall = audio_to_wall(meeting.pauses, at)

    entries: list[tuple[int, str, str, str]] = []
    header: tuple[int, str] | None = None
    before: dict[str, int] = {}
    after: dict[str, int] = {}
    if meeting.transcript_path.exists():
        try:
            text = meeting.transcript_path.read_text(encoding="utf-8")
        except OSError as exc:
            return None, f"could not read {meeting.transcript_path}: {exc}"
        for i, line in enumerate(text.split("\n")):
            parsed = parse_entry(line)
            if parsed is None:
                if header is None and HEADER_RE.match(line):
                    wanted = render_header(meeting, duration_seconds=min(wall, meeting.duration_seconds))
                    if line != wanted:
                        header = (i, wanted)
                continue
            seconds, label, said = parsed
            if seconds >= at:
                entries.append((i, format_timestamp(seconds), label, said))
                after[label] = after.get(label, 0) + 1
            else:
                before[label] = before.get(label, 0) + 1

    audio: dict[str, tuple[int, int]] = {}
    for name in sorted(meeting.audio):
        wav = meeting.dir / str(meeting.audio[name].file)
        if not wav.exists():
            continue
        try:
            with wave.open(str(wav), "rb") as fh:
                frames, rate = fh.getnframes(), fh.getframerate()
        except (OSError, wave.Error) as exc:
            return None, f"could not read {wav}: {exc}"
        keep = int(at * rate)
        if keep < frames:
            audio[name] = (frames, keep)

    snippets: list[Path] = []
    gone: list[str] = []
    channels = meeting.transcription.get("channels") or {}
    for block in channels.values():
        if not isinstance(block, dict):
            continue
        for label, cluster in sorted((block.get("speakers") or {}).items()):
            if not isinstance(cluster, dict):
                continue
            if (
                label not in meeting.speaker_names
                and not cluster.get("echo")
                and not cluster.get("noise")
                and after.get(label)
                and not before.get(label)
            ):
                gone.append(label)
            for snippet in cluster.get("snippets") or []:
                if not isinstance(snippet, dict):
                    continue
                path = meeting.speakers_dir / str(snippet.get("file", ""))
                if (float(snippet.get("start", 0.0)) >= at or label in gone) and path.is_file():
                    snippets.append(path)

    return (
        Plan(
            at=at,
            wall=wall,
            duration_before=meeting.duration_seconds,
            entries=entries,
            header=header,
            audio=audio,
            snippets=snippets,
            speakers_gone=gone,
            pauses_dropped=sum(1 for p in meeting.pauses if p.start >= wall),
            notes=(meeting.dir / paths.NOTES_MD).exists(),
            docs=len(meeting.digest),
        ),
        "",
    )


def warning(meeting: Meeting, plan: Plan) -> str:
    """What somebody is told *before* acting, as one block of prose.

    Said once here and quoted by the dry run and by the window's modal, for the
    reason :func:`referat.cli.delete_warning` exists.
    """
    from referat.transcribe import format_timestamp

    cut = format_timestamp(plan.at)
    lines = [
        f"Everything after {cut} ({clock_of(meeting, plan.wall)} on the clock) is "
        f"discarded from {meeting.id}."
    ]
    if plan.entries:
        lines.append(
            f"{len(plan.entries)} line(s) of {meeting.transcript_path.name}, "
            f"{plan.span}, are removed."
        )
    elif meeting.transcript_path.exists():
        lines.append(f"No line of {meeting.transcript_path.name} is after the cut.")
    for name, (frames, keep) in plan.audio.items():
        rate = meeting.audio[name].samplerate or 1
        lines.append(
            f"{meeting.audio[name].file} is cut from {format_duration(frames / rate)} "
            f"to {format_duration(keep / rate)}; the audio stays on disk."
        )
    if plan.snippets:
        lines.append(f"{len(plan.snippets)} speaker snippet(s) from after the cut are deleted.")
    if plan.speakers_gone:
        lines.append(
            f"{', '.join(plan.speakers_gone)} spoke only after the cut and "
            f"{'is' if len(plan.speakers_gone) == 1 else 'are'} dropped from the meeting's speakers."
        )
    if plan.wall < plan.duration_before - 0.5:
        lines.append(
            f"The recorded duration becomes {format_duration(plan.wall)} "
            f"(was {format_duration(plan.duration_before)})."
        )
    lines.append(
        "Nothing removed is kept anywhere: meta.json records the cut, never the words."
    )
    if plan.notes:
        lines.append(
            "notes.md was written from the untrimmed transcript and may describe what "
            "is discarded. The meeting goes back to `transcribed`; generate the notes "
            "again to replace them"
            + (
                f", and the digest in {plan.docs} linked doc(s) is replaced by the sync "
                f"that follows."
                if plan.docs
                else "."
            )
        )
    return "\n".join(lines)


# --- Acting -------------------------------------------------------------------


def trim(config: Config, meeting_id: str, at: float) -> tuple[bool, str]:
    """Cut one meeting at `at` seconds of audio. The only implementation.

    `referat trim <id> --at HH:MM:SS --apply` is this printed and the window's
    *Trim...* is this in process, in the arrangement every other repair has.

    **Order of writes.** `meta.json` first — the record of the cut, the new
    duration, the pauses, the audio frames, the clusters — through
    :func:`referat.paths.write_json_atomic` directly rather than
    :meth:`referat.meeting.Meeting.save`, because a record that failed to land
    refuses the deletion outright; then the transcript, atomically; then each
    WAV, atomically; then the snippets; then the dashboard. An interruption
    anywhere leaves a record of a cut that the files have not caught up with,
    which the next pass at the same point finishes.

    Returns the value-and-unprefixed-message pair every mutation here returns.
    """
    meeting, why = resolve_meeting(config, meeting_id)
    if meeting is None:
        return False, why
    decided, why = plan(config, meeting, at)
    if decided is None:
        return False, why
    from referat.transcribe import format_timestamp

    cut = format_timestamp(decided.at)
    if decided.nothing:
        return True, (
            f"nothing after {cut} to discard; {meeting.id} already ends at "
            f"{format_duration(meeting.duration_seconds)}"
        )

    record: dict[str, Any] = {
        "at": dt.datetime.now().isoformat(timespec="seconds"),
        "cut": cut,
        "cut_seconds": round(decided.at, 3),
        "wall_seconds": round(decided.wall, 3),
        "clock": clock_of(meeting, decided.wall),
        "duration_before": round(decided.duration_before, 3),
        "entries_removed": len(decided.entries),
        "audio": {
            name: {"frames_before": frames, "frames_after": keep}
            for name, (frames, keep) in decided.audio.items()
        },
        "snippets_removed": len(decided.snippets),
        "speakers_removed": list(decided.speakers_gone),
        "pauses_removed": decided.pauses_dropped,
        "notes_stale": decided.notes,
    }
    previous = meeting.transcription.get(TRIM_KEY)
    meeting.transcription = {
        **meeting.transcription,
        TRIM_KEY: (previous if isinstance(previous, list) else []) + [record],
    }
    if decided.wall < meeting.duration_seconds:
        meeting.duration_seconds = decided.wall
        meeting.ended_at = meeting.started_at + dt.timedelta(seconds=decided.wall)
    meeting.pauses = [p for p in meeting.pauses if p.start < decided.wall]
    for name, (_frames, keep) in decided.audio.items():
        meeting.audio[name].frames = keep
    _drop_clusters(meeting, decided)
    if meeting.status in (MeetingStatus.NOTES_WRITTEN, MeetingStatus.SYNCED):
        meeting.status = MeetingStatus.TRANSCRIBED
    try:
        paths.write_json_atomic(meeting.meta_path, meeting.to_json())
    except OSError as exc:
        return False, f"could not record the cut in {meeting.meta_path}: {exc}"

    if decided.entries or decided.header:
        try:
            text = meeting.transcript_path.read_text(encoding="utf-8")
        except OSError as exc:
            return False, f"could not read {meeting.transcript_path}: {exc}"
        lines = text.split("\n")
        if decided.header is not None:
            lines[decided.header[0]] = decided.header[1]
        from referat.noise import without_lines

        kept = without_lines(lines, {i for i, _at, _label, _text in decided.entries})
        try:
            paths.write_text_atomic(meeting.transcript_path, "\n".join(kept))
        except OSError as exc:
            return False, f"recorded in meta.json but could not write {meeting.transcript_path}: {exc}"

    for name, (_frames, keep) in decided.audio.items():
        wav = meeting.dir / str(meeting.audio[name].file)
        try:
            _truncate_wav(wav, keep)
        except (OSError, wave.Error) as exc:
            return False, f"recorded in meta.json but could not cut {wav}: {exc}"

    dropped = 0
    for path in decided.snippets:
        try:
            path.unlink()
            dropped += 1
        except OSError:
            log.warning("could not delete %s", path, exc_info=True)
    try:
        folder = meeting.speakers_dir
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
    except OSError:
        pass

    index.write_index(config)
    log.info(
        "%s: cut at %s - %d line(s) removed, %d WAV(s) truncated, %d snippet(s) deleted, %s",
        meeting.id,
        cut,
        len(decided.entries),
        len(decided.audio),
        dropped,
        f"{', '.join(decided.speakers_gone)} dropped" if decided.speakers_gone else "no cluster dropped",
    )
    parts = [f"{meeting.id} now ends at {cut}"]
    if decided.entries:
        parts.append(f"{len(decided.entries)} line(s) removed")
    if decided.audio:
        parts.append(f"{', '.join(meeting.audio[n].file for n in decided.audio)} cut")
    if dropped:
        parts.append(f"{dropped} snippet(s) deleted")
    if decided.speakers_gone:
        parts.append(f"{', '.join(decided.speakers_gone)} dropped")
    if decided.notes:
        parts.append("the notes need regenerating")
    return True, "; ".join(parts)


def _drop_clusters(meeting: Meeting, decided: Plan) -> None:
    """Take the gone clusters and the doomed snippet entries out of the record in memory."""
    doomed = {p.name for p in decided.snippets}
    channels = meeting.transcription.get("channels") or {}
    for block in channels.values():
        if not isinstance(block, dict):
            continue
        speakers = block.get("speakers")
        if not isinstance(speakers, dict):
            continue
        for label in decided.speakers_gone:
            speakers.pop(label, None)
            meeting.speaker_names.pop(label, None)
        for cluster in speakers.values():
            if isinstance(cluster, dict) and isinstance(cluster.get("snippets"), list):
                cluster["snippets"] = [
                    s
                    for s in cluster["snippets"]
                    if not (isinstance(s, dict) and str(s.get("file", "")) in doomed)
                ]


def _truncate_wav(path: Path, frames: int) -> None:
    """Keep the first `frames` of a PCM WAV, written whole beside it and swapped in.

    Through the stdlib :mod:`wave` module, which writes a correct header for
    whatever it copied — so a pass interrupted mid-copy leaves the original
    untouched and a `.tmp` beside it, never a file every player rejects.
    """
    tmp = path.with_name(path.name + ".tmp")
    try:
        with wave.open(str(path), "rb") as src, wave.open(str(tmp), "wb") as dst:
            dst.setnchannels(src.getnchannels())
            dst.setsampwidth(src.getsampwidth())
            dst.setframerate(src.getframerate())
            remaining = min(frames, src.getnframes())
            while remaining > 0:
                chunk = src.readframes(min(remaining, COPY_FRAMES))
                if not chunk:
                    break
                dst.writeframes(chunk)
                remaining -= len(chunk) // (src.getsampwidth() * src.getnchannels())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
