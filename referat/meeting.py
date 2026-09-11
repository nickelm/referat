"""The meeting record and its `meta.json` file.

One folder per meeting, one JSON document describing it. No audio and no threads
live here: :mod:`referat.recorder` fills these objects in while capturing,
:mod:`referat.transcribe` updates the status and the `transcription` block, and
`referat list` reads them back.

The status vocabulary is deliberately *not* :class:`referat.state.State`. That
enum describes the recorder — which has a `paused` state — while this one
describes what is on disk, where a paused meeting is still being recorded.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from referat import __version__, paths

if TYPE_CHECKING:
    from referat.config import Config

log = logging.getLogger(__name__)


class MeetingStatus(StrEnum):
    r"""The meeting's lifecycle, and the `status` key of `meta.json`.

    ::

        recording -> recorded -> transcribing -> gate_failed | transcribed
                                                            -> notes_written -> synced
        failed  (transcription raised — a different thing from gate_failed)

    **One field, one authority.** Every surface renders this value and none of
    them infers a state from which files happen to exist. A parallel `state` key
    beside it would be a second place to say what a meeting is, which is the
    mistake `voices_dir`, `format_duration` and the VS Code extension's
    deliberately absent meetings-folder setting were each pulled back from.

    :attr:`GATE_FAILED` is what earned the widening. A meeting whose transcript
    failed the quality gate keeps its audio and stays in staging, and used to be
    written down as `done` — indistinguishable in the file from a meeting that
    finished cleanly. Telling them apart meant asking three questions at once
    (status, are the WAVs there, is the folder still in `%LOCALAPPDATA%`), an
    inference that lived in no function and was re-derived by every reader.

    Still deliberately *not* :class:`referat.state.State`. That enum describes the
    recorder, which has a `paused` state; this one describes what is on disk,
    where a paused meeting is still being recorded.
    """

    RECORDING = "recording"
    RECORDED = "recorded"
    TRANSCRIBING = "transcribing"
    GATE_FAILED = "gate_failed"
    """Transcribed, but the quality gate refused to delete the audio, so the
    meeting kept its WAVs and stayed in staging. Written by the pipeline; cleared
    by a `referat rerun` that comes out clean."""
    TRANSCRIBED = "transcribed"
    NOTES_WRITTEN = "notes_written"
    """`/cleanup` has written `notes.md`. The one transition no pipeline can make,
    because that pass is forbidden from touching `meta.json` — whoever spawned it
    calls `referat state <id> notes-written` afterwards."""
    SYNCED = "synced"
    """Every doc of every one of this meeting's tags is current. Build step 13."""
    FAILED = "failed"
    """Transcription raised. A different thing from :attr:`GATE_FAILED`, which is
    a transcript that came out but was not trusted."""


LEGACY_STATUS = {"stopped": MeetingStatus.RECORDED, "done": MeetingStatus.TRANSCRIBED}
"""What the pre-lifecycle vocabulary maps onto, applied by :meth:`Meeting.load`.

**A pure mapping, with no look at the filesystem.** It is tempting to notice that
a `done` meeting still holding WAVs in staging is really `gate_failed` and say so
here — but that is the three-way inference being removed, and doing it in the
loader would only hide it. The few such meetings on this machine are left alone;
their next `rerun` writes the right value.
"""


@dataclass
class Pause:
    """One pause, in seconds of wall-clock time elapsed since `started_at`.

    `end` stays None while the meeting is still paused, so a process killed
    mid-pause leaves a record of what was happening rather than a silent gap.
    """

    start: float
    end: float | None = None

    @property
    def duration(self) -> float:
        return 0.0 if self.end is None else max(0.0, self.end - self.start)

    def to_json(self) -> dict[str, Any]:
        end = None if self.end is None else round(self.end, 3)
        return {"start": round(self.start, 3), "end": end}

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> Pause:
        end = raw.get("end")
        return cls(start=float(raw.get("start", 0.0)), end=None if end is None else float(end))


@dataclass
class ChannelAudio:
    """What was captured on one channel, for the `audio` block of `meta.json`."""

    file: str
    samplerate: int
    channels: int = 1
    frames: int = 0
    device: str = ""

    @property
    def duration_seconds(self) -> float:
        return self.frames / self.samplerate if self.samplerate else 0.0

    def to_json(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "samplerate": self.samplerate,
            "channels": self.channels,
            "frames": self.frames,
            "duration_seconds": round(self.duration_seconds, 3),
            "device": self.device,
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> ChannelAudio:
        return cls(
            file=str(raw.get("file", "")),
            samplerate=int(raw.get("samplerate", 0)),
            channels=int(raw.get("channels", 1)),
            frames=int(raw.get("frames", 0)),
            device=str(raw.get("device", "")),
        )


@dataclass
class Meeting:
    """One meeting folder: where it is, when it ran, and what is in it."""

    dir: Path
    id: str
    started_at: dt.datetime
    ended_at: dt.datetime | None = None
    duration_seconds: float = 0.0
    status: MeetingStatus = MeetingStatus.RECORDING
    pauses: list[Pause] = field(default_factory=list)
    audio: dict[str, ChannelAudio] = field(default_factory=dict)
    missing_channels: list[str] = field(default_factory=list)
    """Channels that would not open at all this run, as a written fact.

    A channel that never opened shows up in `audio` only as an *absent* key, and
    inferring "no microphone" from a missing key is exactly the shape this
    codebase refuses for the lifecycle: nobody inferred it, so a meeting with no
    microphone in it read as `transcribed` and no surface ever said otherwise.
    Twenty-four minutes went that way on 2026-09-02. It is `["mic"]` far more
    often than the other way round, and for a meeting held in a room that means
    nothing was recorded.
    """
    transcription: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    """The project ids this meeting carries. Absent or empty means *untagged*.

    Ids rather than names, so a `referat project rename` touches `projects.json`
    alone and no meeting record. A list rather than a single value, because a
    meeting may belong to several threads of work at once; and *untagged* is the
    computed state of this list being empty, never an id in it. See
    :mod:`referat.projects`.
    """
    speaker_names: dict[str, str] = field(default_factory=dict)
    """`SPEAKER_NN` to the name it was resolved to, for the speakers that have one.

    Kept here rather than only in the rewritten Markdown because it is the only
    way back to the number: `referat label --forget <name>` has to know which
    label to revert, and a `referat rerun` re-diarizes and renumbers from scratch.
    """
    digest: dict[str, dict[str, Any]] = field(default_factory=dict)
    """What has been pushed into which Google Doc, and from which bytes of the notes.

    Keyed by **`gdoc_id`**, each entry `{tab_id, notes_sha256, written_at}`.
    Build step 13 writes it; nothing before that reads it, and an absent key is
    the ordinary state of every meeting written before the digests existed.

    **Keyed by doc rather than being one flat object**, because a meeting fans
    out: it is written into every doc of every project it carries, so it can be
    current in one and stale in another, and one flat object could not say that.

    `notes_sha256` is what makes reconciliation cheap and is the whole reason
    this is a record rather than a flag. A sync needs `meta.json` plus one
    `documents.get` per doc and never has to re-read the doc's prose to work out
    what changed: a block whose stored sha still matches the `notes.md` on disk
    is current, and one whose sha has moved is re-rendered in place. It is the
    same move `transcription.debleed` makes -- write down what was done, so the
    next pass can tell what is left to do.

    See :meth:`referat.digest.is_synced`, which is the predicate behind the
    `synced` lifecycle state, and note that it counts an **archived** project's
    docs exactly as it counts a live one's: hiding is presentation and never a
    constraint.
    """
    referat_version: str = __version__

    # --- Files in the folder ------------------------------------------------

    @property
    def meta_path(self) -> Path:
        return self.dir / paths.META_JSON

    @property
    def mic_path(self) -> Path:
        return self.dir / paths.MIC_WAV

    @property
    def system_path(self) -> Path:
        return self.dir / paths.SYSTEM_WAV

    @property
    def transcript_path(self) -> Path:
        return self.dir / paths.TRANSCRIPT_MD

    @property
    def speakers_dir(self) -> Path:
        """Where the snippets of not-yet-named speakers live. See :data:`referat.paths.SPEAKERS_DIR`."""
        return self.dir / paths.SPEAKERS_DIR

    # --- Lifecycle ----------------------------------------------------------

    @classmethod
    def create(cls, config: Config, started_at: dt.datetime | None = None) -> Meeting:
        """Make the folder and return the meeting, without writing `meta.json` yet.

        Created in the **staging** folder, never in the meetings folder: recording
        writes WAVs, and the meetings folder may be synced. See
        :meth:`referat.config.Config.staging_dir`.

        The id is the folder name, so it carries any `_2` collision suffix
        :func:`referat.paths.new_meeting_dir` had to add — and that suffix accounts
        for both roots, so the id survives the later move unchanged.
        """
        started_at = started_at or dt.datetime.now()
        folder = paths.new_meeting_dir(
            config.staging_dir(), started_at, avoid=config.meeting_roots()
        )
        log.info("meeting folder %s", folder)
        return cls(dir=folder, id=folder.name, started_at=started_at)

    def save(self) -> None:
        """Write `meta.json` atomically.

        Never raises: a failed metadata write must not take a recording down
        with it, because the audio is the part that cannot be reconstructed.
        """
        try:
            paths.write_json_atomic(self.meta_path, self.to_json())
        except OSError:
            log.exception("could not write %s", self.meta_path)

    @classmethod
    def load(cls, meeting_dir: Path) -> Meeting | None:
        """Read one meeting folder's `meta.json`, or None when it is unusable.

        Tolerant on purpose: unknown keys are ignored and missing ones take
        their defaults, so a folder written by a later version still loads.
        """
        meta = meeting_dir / paths.META_JSON
        try:
            raw: Any = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            log.warning("cannot read %s", meta)
            return None
        if not isinstance(raw, dict):
            log.warning("ignoring malformed %s", meta)
            return None

        raw_status = str(raw.get("status", MeetingStatus.RECORDED))
        try:
            status = LEGACY_STATUS.get(raw_status) or MeetingStatus(raw_status)
        except ValueError:
            log.warning("unknown status %r in %s", raw_status, meta)
            status = MeetingStatus.RECORDED

        audio_raw = raw.get("audio") or {}
        audio = {k: ChannelAudio.from_json(v) for k, v in audio_raw.items() if isinstance(v, dict)}
        return cls(
            dir=meeting_dir,
            id=str(raw.get("id", meeting_dir.name)),
            started_at=_parse_time(raw.get("started_at")) or _folder_time(meeting_dir),
            ended_at=_parse_time(raw.get("ended_at")),
            duration_seconds=float(raw.get("duration_seconds", 0.0) or 0.0),
            status=status,
            pauses=[Pause.from_json(p) for p in raw.get("pauses", []) if isinstance(p, dict)],
            audio=audio,
            transcription=raw.get("transcription") or {},
            missing_channels=[
                str(c) for c in (raw.get("missing_channels") or []) if str(c).strip()
            ],
            tags=[str(t) for t in (raw.get("tags") or []) if str(t).strip()],
            digest={
                str(k): dict(v)
                for k, v in (raw.get("digest") or {}).items()
                if isinstance(v, dict)
            },
            speaker_names={
                str(k): str(v) for k, v in (raw.get("speaker_names") or {}).items()
            },
            referat_version=str(raw.get("referat_version", "")),
        )

    # --- Serialization ------------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        """The `meta.json` document, keys in the order the folder contract lists them."""
        return {
            "id": self.id,
            "started_at": self.started_at.isoformat(timespec="seconds"),
            "ended_at": self.ended_at.isoformat(timespec="seconds") if self.ended_at else None,
            "duration_seconds": round(self.duration_seconds, 3),
            "status": str(self.status),
            "pauses": [p.to_json() for p in self.pauses],
            "audio": {name: channel.to_json() for name, channel in self.audio.items()},
            "missing_channels": self.missing_channels,
            "transcription": self.transcription,
            "tags": self.tags,
            "digest": self.digest,
            "speaker_names": self.speaker_names,
            "referat_version": self.referat_version,
        }


def format_duration(seconds: float) -> str:
    """`M:SS`, or `H:MM:SS` once a meeting runs past the hour.

    Lives here rather than in :mod:`referat.cli` because `referat list` and the
    meetings folder's generated `INDEX.md` both print it, and two copies would
    eventually disagree about the same meeting. Deliberately not
    :func:`referat.transcribe.format_timestamp`, which is the same three lines at
    the cost of importing the whole transcription stack into commands that only
    read JSON.
    """
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def audio_to_wall(pauses: list[Pause], audio: float) -> float:
    """Seconds of audio (pauses excluded) into wall-clock seconds since the start.

    The two clocks :class:`referat.recorder.RecordingClock` keeps, made
    inter-convertible from the pause list `meta.json` records, which is what the
    folder contract has promised since build step 3 and which nothing needed
    until `referat trim` took a cut as a transcript timestamp and had to say
    what time of day that was.

    A position exactly at the start of a pause maps to the instant the pause
    *began* rather than the instant it ended — both are the same audio second,
    and the earlier one is the honest end of a meeting cut there.
    """
    wall = audio
    for pause in sorted(pauses, key=lambda p: p.start):
        if pause.start < wall:
            wall += pause.duration
        else:
            break
    return wall


def wall_to_audio(pauses: list[Pause], wall: float) -> float:
    """Wall-clock seconds since the start into seconds of audio. The inverse of :func:`audio_to_wall`.

    An instant inside a pause maps to the audio second the pause began at,
    which is where every later sample of that instant went. An open pause —
    `end` still None — runs to the instant asked about.
    """
    paused = 0.0
    for pause in sorted(pauses, key=lambda p: p.start):
        if pause.start >= wall:
            break
        end = wall if pause.end is None else pause.end
        paused += max(0.0, min(end, wall) - pause.start)
    return max(0.0, wall - paused)


def _parse_time(value: Any) -> dt.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        return None


def _folder_time(meeting_dir: Path) -> dt.datetime:
    """Fall back to the folder name when `started_at` is missing or unparseable."""
    try:
        return paths.parse_meeting_id(meeting_dir.name)
    except ValueError:
        return dt.datetime.fromtimestamp(meeting_dir.stat().st_mtime)


def resolve_meeting(config: Config, meeting_id: str) -> tuple[Meeting | None, str]:
    """One meeting by id across both roots: the meeting, or None and why not.

    Every command that takes a meeting id needs this and needs to say the same two
    things about it — that there is no such meeting, or that its `meta.json` will
    not read — so the lookup and both sentences live here once. The *caller* adds
    its own `referat <command>:` prefix, which is the only part that differs and
    the reason this returns the complaint rather than printing it.

    Both roots, always: a meeting lives in staging while it still has audio, and
    resolving against the meetings folder alone would fail on exactly the meetings
    that need attention.
    """
    folder = paths.find_meeting_dir(config.meeting_roots(), meeting_id)
    if folder is None:
        return None, f"no meeting {meeting_id}"
    meeting = Meeting.load(folder)
    if meeting is None:
        return None, f"cannot read {folder / paths.META_JSON}"
    return meeting, ""


def undiarized_channels(meeting: Meeting) -> list[str]:
    """The channels whose diarization *failed* — not the ones it skipped.

    Read off the `transcription.channels.<name>.diarization.status` the
    pipeline wrote, never off the labels in `transcript.md`. `skipped` is a
    deliberate no-op — diarization off, no token, a channel with no voice in
    it — and costs nothing worth keeping audio for; `failed` is a run that
    should have produced speakers and did not, and those speakers can only
    ever come back from the WAV. That is the distinction
    :func:`referat.transcribe.audio_is_clean` keeps the audio on, and the one
    `referat promote` warns about.
    """
    channels = meeting.transcription.get("channels") or {}
    return sorted(
        name
        for name, block in channels.items()
        if isinstance(block, dict) and (block.get("diarization") or {}).get("status") == "failed"
    )


def sac_block(meeting: Meeting) -> str:
    """The native module Smart App Control refused in this meeting's last run, or `""`.

    :attr:`referat.diarize.Diarization.blocked`, read back out of `meta.json`
    across the channels. Non-empty is the tray's cue that the audio was kept
    for a reason that will lift by itself — see `App._recover_blocked` — as
    opposed to a gate the transcript failed on its merits.
    """
    channels = meeting.transcription.get("channels") or {}
    for block in channels.values():
        if isinstance(block, dict):
            blocked = (block.get("diarization") or {}).get("blocked")
            if blocked:
                return str(blocked)
    return ""


def load_meetings(config: Config) -> list[Meeting]:
    """Every readable meeting, oldest first, across both roots.

    Takes the config rather than a folder because a meeting lives in the staging
    folder until its audio is released and in the meetings folder afterwards — see
    :meth:`referat.config.Config.meeting_roots`. Listing only one of the two would
    hide exactly the meetings that need attention.
    """
    dirs = [d for root in config.meeting_roots() for d in paths.list_meeting_dirs(root)]
    found = [Meeting.load(d) for d in sorted(dirs, key=lambda p: p.name)]
    return [m for m in found if m is not None]


def reconcile_interrupted(config: Config) -> list[Meeting]:
    """Clamp meetings left mid-transcription by a process that is gone.

    `transcribing` is the one lifecycle value no restart can inherit. Jobs run in
    daemon threads inside the tray, so a process that is not running has no job
    running either: any meeting still claiming that status was abandoned by a
    kill, a crash, a power loss, or a resume the CUDA context did not survive.
    `transcribe_meeting`'s own handler covers the interrupts it *can* see, and
    `CLAUDE.md` records the rest as a known gap — "a hard kill can still leave
    it, and no handler inside a process can promise otherwise". This is that gap
    closed from the other end: not by a handler promising more than it can, but
    by the next start reading the residue and correcting it.

    It matters beyond tidiness because the lie is sticky. A meeting stuck at
    `transcribing` is refused by `referat delete`, rendered as in-flight by every
    surface that draws the lifecycle, and counted as busy by
    :func:`referat.rerun.busy_tray` — so the one command that would repair it is
    the one it blocks.

    The clamp is :attr:`MeetingStatus.RECORDED` and the reasoning is
    `transcribe_meeting`'s, deliberately: nothing on disk was changed by the run
    that died, so what describes the folder is what described it before —  audio
    and no transcript this process is willing to vouch for. `interrupted` is
    written the same way and for the same reason, so an abandoned run is recorded
    rather than hidden; `why` names this path so it is distinguishable from a
    Ctrl+C, which is a different story about the same field.

    **The caller must have established that no live tray is transcribing** —
    :func:`referat.rerun.busy_tray` is that question — because this cannot tell a
    dead process's residue from another process's work in progress.

    **A meeting left `recording` is the same residue one state earlier, since
    2026-09-11**, when a tray was killed seventy seconds into
    `2026-09-11_0835`. The recorder streams both WAVs to disk and reseals their
    headers every two seconds, so everything captured was there; what was not
    was an `ended_at`, a duration, honest frame counts and a status anything
    would act on — `recording` is refused by `referat delete`, drawn as live by
    every surface, and re-queued by nothing. So this closes the meeting out the
    way :meth:`referat.recorder.Recorder.stop` would have: the headers sealed
    from the files' own length (:func:`referat.recorder.seal_wav`), the frames
    read back from them, the end taken from the last write to either WAV — the
    last moment anything is known to have been captured — an open pause closed
    there, and `recorded`, which is what puts it in front of :meth:`App._resume`
    to be transcribed. Unlike the `transcribing` clamp this does change the
    folder, by exactly the header bytes a clean stop writes and nothing else.

    :func:`busy_tray` answers *transcribing* and deliberately not *recording*,
    so this branch asks its own question: a `status.json` from a live tray that
    names this meeting while recording or paused means the meeting is not
    residue but somebody's meeting in progress, and it is left alone.
    """
    repaired: list[Meeting] = []
    for meeting in load_meetings(config):
        if meeting.status is MeetingStatus.RECORDING:
            if _close_out_killed_recording(meeting):
                repaired.append(meeting)
            continue
        if meeting.status is not MeetingStatus.TRANSCRIBING:
            continue
        meeting.status = MeetingStatus.RECORDED
        meeting.transcription = {
            **meeting.transcription,
            "interrupted": {
                "at": dt.datetime.now().isoformat(timespec="seconds"),
                "why": "the transcribing process did not survive",
            },
        }
        try:
            meeting.save()
        except Exception:
            # A meeting that cannot be written back is worth a line, never a tray
            # that refuses to start: the recorder comes up regardless.
            log.exception("could not reconcile %s", meeting.id)
            continue
        log.warning(
            "%s was left transcribing by a process that is gone; back to recorded", meeting.id
        )
        repaired.append(meeting)
    return repaired


def _close_out_killed_recording(meeting: Meeting) -> bool:
    """Finish a meeting whose recorder died. True when it was written back as `recorded`."""
    from referat import status
    from referat.recorder import seal_wav
    from referat.state import State

    live = status.read_status()
    if (
        live is not None
        and live.meeting_id == meeting.id
        and live.state in (State.RECORDING, State.PAUSED)
        and status.is_running(live.pid)
    ):
        return False

    last_write: dt.datetime | None = None
    for channel in meeting.audio.values():
        path = meeting.dir / channel.file
        if not path.exists():
            continue
        # Read before sealing: the seal rewrites the header and moves the mtime.
        written = dt.datetime.fromtimestamp(path.stat().st_mtime)
        try:
            channel.frames = seal_wav(path)
        except (OSError, ValueError):
            log.exception("%s: could not seal %s", meeting.id, channel.file)
            continue
        last_write = written if last_write is None else max(last_write, written)

    meeting.ended_at = last_write if last_write is not None else meeting.started_at
    meeting.duration_seconds = max(0.0, (meeting.ended_at - meeting.started_at).total_seconds())
    if meeting.pauses and meeting.pauses[-1].end is None:
        meeting.pauses[-1].end = max(meeting.pauses[-1].start, meeting.duration_seconds)
    meeting.status = MeetingStatus.RECORDED
    meeting.transcription = {
        **meeting.transcription,
        "interrupted": {
            "at": dt.datetime.now().isoformat(timespec="seconds"),
            "why": "the recording process did not survive",
            "after_seconds": round(meeting.duration_seconds, 1),
        },
    }
    try:
        meeting.save()
    except Exception:
        log.exception("could not reconcile %s", meeting.id)
        return False
    log.warning(
        "%s was left recording by a process that is gone; closed out at %.1fs as recorded",
        meeting.id,
        meeting.duration_seconds,
    )
    return True
