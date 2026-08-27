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
from typing import Any

from referat import __version__, paths

log = logging.getLogger(__name__)


class MeetingStatus(StrEnum):
    """The `status` key of `meta.json`, as fixed by the meeting folder contract."""

    RECORDING = "recording"
    STOPPED = "stopped"
    TRANSCRIBING = "transcribing"
    DONE = "done"
    FAILED = "failed"


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
    transcription: dict[str, Any] = field(default_factory=dict)
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

    # --- Lifecycle ----------------------------------------------------------

    @classmethod
    def create(cls, meetings_dir: Path, started_at: dt.datetime | None = None) -> Meeting:
        """Make the folder and return the meeting, without writing `meta.json` yet.

        The id is the folder name, so it carries any `_2` collision suffix
        :func:`referat.paths.new_meeting_dir` had to add.
        """
        started_at = started_at or dt.datetime.now()
        folder = paths.new_meeting_dir(meetings_dir, started_at)
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

        try:
            status = MeetingStatus(raw.get("status", MeetingStatus.STOPPED))
        except ValueError:
            log.warning("unknown status %r in %s", raw.get("status"), meta)
            status = MeetingStatus.STOPPED

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
            "transcription": self.transcription,
            "referat_version": self.referat_version,
        }


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


def load_meetings(meetings_dir: Path) -> list[Meeting]:
    """Every readable meeting in the meetings folder, oldest first."""
    found = [Meeting.load(d) for d in paths.list_meeting_dirs(meetings_dir)]
    return [m for m in found if m is not None]
