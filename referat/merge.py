"""Interleaving the channels into one chronological transcript.

Both channels are written against a single recording clock and padded with real
silence whenever a device delivers nothing — see :mod:`referat.recorder` — so the
same position in `mic.wav` and in `system.wav` is the same moment of the meeting.
Merging is therefore an ordering problem, not an alignment one, and this module
is correspondingly small.

It exists as its own module anyway, for two reasons. `transcript.md` should have
exactly one author, and the loopback channel's lines are relabeled per speaker
here: :mod:`referat.diarize` writes a `speaker` onto each segment it could
attribute, and that wins over the channel's own label when the line is rendered.

**No runtime import of :mod:`referat.transcribe`.** It is the other way round:
`transcribe` imports this module at top level, and the type it passes in comes in
here only under `TYPE_CHECKING`. Importing this module stays free of the
`transcribe` extra's ~3 GB of wheels.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from referat.transcribe import ChannelTranscript

CHANNEL_ORDER = ("mic", "system")
"""Tie-break for segments that start at the same instant.

The two channels really do overlap — people talk over each other — and without a
fixed order the transcript would depend on which channel a dict happened to list
first. `ME` before `REMOTE` when they collide, which is also the order the two
are transcribed in.
"""


def channel_rank(channel: str) -> int:
    """Sort position of a channel, with anything unknown ordered last."""
    try:
        return CHANNEL_ORDER.index(channel)
    except ValueError:
        return len(CHANNEL_ORDER)


def merge(transcripts: list[ChannelTranscript]) -> list[tuple[float, str, str]]:
    """Flatten channels into one chronological list of `(start, label, text)`.

    The shape :func:`referat.transcribe.render_transcript` takes. Sorting is by
    start time, then by channel, then by end time, so the result is fully
    determined by the input rather than by dict or list order.

    A segment's own `speaker` is used when diarization gave it one, and the
    channel's label otherwise — `ME` for the microphone always, `REMOTE` for a
    loopback line that was never attributed to anybody.
    """
    entries = [
        (
            segment.start,
            segment.end,
            channel_rank(t.channel),
            segment.speaker or t.label,
            segment.text,
        )
        for t in transcripts
        for segment in t.segments
    ]
    entries.sort(key=lambda e: (e[0], e[2], e[1]))
    return [(start, label, text) for start, _end, _rank, label, text in entries]
