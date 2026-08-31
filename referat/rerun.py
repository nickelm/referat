"""`referat rerun` — transcribing a meeting again from the audio it kept.

The pipeline keeps both WAVs whenever it is not certain of what it produced: a
channel that failed outright, a transcript that looks garbled by
faster-whisper's own scores, a meeting whose diarization went nowhere. That is
what this command is for. It is also how a transcript is regenerated after the
model, the language or the diarization checkpoint in `config.toml` changes.

**It only ever re-derives.** Nothing here edits a transcript — it hands the
meeting to :func:`referat.transcribe.transcribe_meeting`, which writes a new
`transcript.md` from the audio through :func:`referat.paths.write_text_atomic`,
so a run that fails leaves the previous one intact rather than half of a new one.
That is a different act from `referat label`, which rewrites the label field of
an existing transcript and nothing else.

**A rerun renumbers.** Diarization clusters from scratch, so this run's
`SPEAKER_02` is not last run's, and the old `speaker_names` and `speakers/`
snippets are cleared before the pipeline starts rather than carried over. No name
is lost by that: every one of them is in the known-voices database, and
identification looks each cluster up there again on the way through. Carrying the
old mapping over is the one thing that *would* lose them — it would put last
week's name on this run's numbering.

**Two large-v3 models do not fit in 12 GB of VRAM.** `transcribe._RUN_LOCK`
serializes jobs inside one process and cannot see across one, so this command
reads `status.json` and refuses while a live tray is transcribing. `--force`
overrides it, because the guard is about the GPU rather than about correctness.

The `transcribe` extra is imported inside :func:`run` rather than at module
scope, so `referat list` and `referat status` stay free of three gigabytes of
torch.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from referat import paths, status, voices
from referat.config import Config
from referat.logging_setup import setup_logging
from referat.meeting import Meeting
from referat.state import State

log = logging.getLogger(__name__)


def busy_tray() -> str | None:
    """The meeting a live tray is transcribing, `""` when it is busy unnamed, else None.

    None means nothing is in the way: no status file, a stale one from a tray
    that is gone, or a tray that is idle or recording. Recording is deliberately
    not in the way — capture costs no GPU, and refusing during it would mean the
    command is unusable through most of a working day.
    """
    current = status.read_status()
    if current is None or not status.is_running(current.pid):
        return None
    if current.state is State.TRANSCRIBING or current.jobs > 0:
        return current.meeting_id or ""
    return None


def clear_speakers(meeting: Meeting) -> None:
    """Drop the previous run's speaker labels and snippets. Does not save.

    Both are per-run facts: diarization renumbers from scratch, so keeping either
    would attach last run's answers to this run's numbering. The names survive in
    the known-voices database, where identification will find them again.
    """
    meeting.speaker_names = {}
    folder = meeting.speakers_dir
    if not folder.is_dir():
        return
    for snippet in sorted(folder.glob("*.wav")):
        try:
            snippet.unlink()
        except OSError:
            log.warning("could not delete %s", snippet, exc_info=True)
    try:
        if not any(folder.iterdir()):
            folder.rmdir()
    except OSError:
        pass


def audio_present(meeting: Meeting) -> list[Path]:
    """The channel WAVs still on disk. Empty means there is nothing to re-run from."""
    return [p for p in (meeting.mic_path, meeting.system_path) if p.exists()]


def run(config: Config, meeting_id: str, *, force: bool = False) -> int:
    """`referat rerun <meeting-id> [--force]`. Returns the process exit code."""
    folder = paths.find_meeting_dir(config.meeting_roots(), meeting_id)
    if folder is None:
        where = " or ".join(str(r) for r in config.meeting_roots())
        print(
            f"referat rerun: no meeting {meeting_id} in {where}\n"
            f"               run `referat list` to see what is there",
            file=sys.stderr,
        )
        return 1
    meeting = Meeting.load(folder)
    if meeting is None:
        print(f"referat rerun: cannot read {folder / paths.META_JSON}", file=sys.stderr)
        return 1

    audio = audio_present(meeting)
    if not audio:
        released = bool(meeting.transcription.get("audio_released"))
        why = (
            "its audio was released once the transcript came out clean"
            if released
            else "its audio is gone"
        )
        print(
            # Plain ASCII in everything printed, as `label.py` also does: this
            # console's code page is not UTF-8 and a dash is not worth a
            # UnicodeEncodeError.
            f"referat rerun: nothing to re-transcribe in {meeting.id}: {why}.\n"
            f"               transcript.md is what is left of it; "
            f"`referat label {meeting.id}` can still name its speakers",
            file=sys.stderr,
        )
        return 1

    busy = busy_tray()
    if busy is not None and not force:
        doing = f"transcribing {busy}" if busy else "already transcribing"
        print(
            f"referat rerun: the tray is {doing}; two models do not fit on the GPU.\n"
            f"               wait for it to finish, or pass --force",
            file=sys.stderr,
        )
        return 1

    setup_logging(config.app.log_level)
    from referat.transcribe import TranscriptionError, transcribe_meeting

    clear_speakers(meeting)
    meeting.save()

    print(f"Re-transcribing {meeting.id} from {', '.join(p.name for p in audio)}...")
    try:
        meeting = transcribe_meeting(meeting, config)
    except TranscriptionError as exc:
        # meta.json already says `failed`, and both WAVs are still there:
        # transcribe_meeting writes that down before it raises, precisely so
        # this command can be run again.
        print(f"referat rerun: {exc}", file=sys.stderr)
        return 1

    seconds = meeting.transcription.get("seconds")
    backend = meeting.transcription.get("model", "?")
    device = meeting.transcription.get("device", "?")
    took = f" in {seconds:.0f}s" if isinstance(seconds, (int, float)) else ""
    print(f"{meeting.id}: {meeting.status}{took} ({backend} on {device})")

    unknown = voices.unknown_speakers(meeting)
    if unknown:
        print(
            f"{len(unknown)} unnamed speaker(s): {', '.join(unknown)}\n"
            f"run: referat label {meeting.id}"
        )
    return 0
