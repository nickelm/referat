r"""Splitting the loopback channel into speakers, with `pyannote.audio`.

`mic.wav` is one speaker by definition — the owner of the laptop — but
`system.wav` carries everyone else on the call in a single mono stream. This
module says who spoke when in it, so :mod:`referat.merge` can label the remote
lines `SPEAKER_01`, `SPEAKER_02`, ... instead of an undifferentiated `REMOTE`.

**Nothing here ever raises.** Diarization is a nicety layered on a pipeline that
already works: a missing token, a gated repository, an out-of-memory, a pyannote
release that moved its API — all of them must cost speaker labels and nothing
else. :func:`diarize` therefore reports failure in its return value, and the one
thing it must never do is let an exception escape. On the CUDA attempt that
exception would reach :func:`referat.transcribe.transcribe_meeting`, which would
dutifully re-transcribe the entire meeting on the CPU because it cannot tell a
diarization problem from a dying GPU.

**Audio comes in already decoded**, as the float32 array
:func:`referat.transcribe.decode_wav` produces. That keeps this module free of
any import of `transcribe` — the dependency runs the other way — and it keeps
pyannote away from file decoding, which matters more than it sounds: handed a
path, pyannote reaches for `torchcodec` and its bundled FFmpeg, which **Smart App
Control blocks on this machine**, exactly as it blocks PyAV's. Handed
``{"waveform": ..., "sample_rate": ...}`` it decodes nothing at all.

**Imports are lazy**, as in :mod:`referat.transcribe`: `torch` and
`pyannote.audio` live behind the `transcribe` extra, and importing this module
must stay free for a base install.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

import numpy as np

from referat.config import Config

if TYPE_CHECKING:
    from referat.transcribe import Segment

log = logging.getLogger(__name__)

SPEAKER_PREFIX = "SPEAKER_"
"""Renumbered labels are `SPEAKER_01`, `SPEAKER_02`, ..., per the folder contract.

pyannote's own labels are zero-based and in no particular order, so they are not
used directly. See :func:`assign`.
"""

MAX_REASON = 200
"""How much of a failure gets written into `meta.json`, in characters.

pyannote's own errors are helpful and enormous — the gated-repository one is a
four-hundred character block with a request id and three suggested fixes in it.
The full text goes to the log, where there is room for it; `meta.json` keeps the
first line's worth, which is the part that says what went wrong.
"""

DONE = "done"
SKIPPED = "skipped"
FAILED = "failed"
"""The `status` of a :class:`Diarization`, and of the `diarization` block in
`meta.json`. `skipped` is a deliberate no-op — diarization turned off, or no
Hugging Face token — while `failed` is something that went wrong."""


@dataclass(frozen=True)
class Turn:
    """One stretch of one speaker, in seconds into the channel's WAV file.

    Same clock as :class:`referat.transcribe.Segment`: audio time, with the
    meeting's pauses already excluded because the recorder stops appending
    samples while paused.
    """

    start: float
    end: float
    speaker: str


@dataclass(frozen=True)
class Diarization:
    """What the pipeline had to say about one channel, successful or not."""

    status: str = SKIPPED
    reason: str = ""
    """Why, in words, for the log and for `meta.json`. Empty on success."""
    model: str = ""
    device: str = ""
    seconds: float = 0.0
    turns: list[Turn] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == DONE

    def to_json(self) -> dict[str, Any]:
        meta: dict[str, Any] = {"status": self.status}
        if self.reason:
            meta["reason"] = self.reason
        if self.model:
            meta["model"] = self.model
            meta["device"] = self.device
            meta["seconds"] = round(self.seconds, 2)
            meta["turns"] = len(self.turns)
        return meta


# --- Running the pipeline ---------------------------------------------------


def diarize(audio: np.ndarray, sample_rate: int, config: Config, device: str) -> Diarization:
    """Who spoke when in `audio`, as a list of :class:`Turn`.

    `audio` is mono float32, the shape :func:`referat.transcribe.decode_wav`
    returns; `device` is the one the transcription backend resolved to, so
    diarization follows Whisper onto the GPU or onto the CPU rather than
    deciding for itself.

    Never raises. A run that could not happen comes back with `status`
    :data:`SKIPPED` or :data:`FAILED` and a reason, and the caller keeps its
    undifferentiated `REMOTE` labels.
    """
    if not config.transcription.diarization:
        return Diarization(reason="diarization is off in the config")
    model = config.transcription.diarization_model.strip()
    if not model:
        return Diarization(reason="no [transcription].diarization_model configured")
    token = config.hf_token()
    if not token:
        return Diarization(reason=f"no Hugging Face token in {config.paths.hf_token_file}")

    started = time.monotonic()
    try:
        turns = _run(audio, sample_rate, model, token, device)
    except Exception as exc:
        # Deliberately bare. Anything at all going wrong here — a gated
        # repository, a network failure, an out-of-memory, a pyannote release
        # that renamed something — costs the speaker labels and stops there.
        log.exception("diarization failed")
        return Diarization(
            status=FAILED,
            reason=_reason(exc),
            model=model,
            device=device,
            seconds=time.monotonic() - started,
        )

    elapsed = time.monotonic() - started
    speakers = sorted({t.speaker for t in turns})
    log.info(
        "diarized %.1fs of audio in %.1fs: %d turns, %d speaker(s) on %s",
        audio.size / sample_rate if sample_rate else 0.0,
        elapsed,
        len(turns),
        len(speakers),
        device,
    )
    return Diarization(status=DONE, model=model, device=device, seconds=elapsed, turns=turns)


def _reason(exc: Exception) -> str:
    """One readable line naming what went wrong. See :data:`MAX_REASON`."""
    text = " ".join(f"{type(exc).__name__}: {exc}".split())
    return text if len(text) <= MAX_REASON else text[: MAX_REASON - 1].rstrip() + "…"


def _run(
    audio: np.ndarray, sample_rate: int, model: str, token: str, device: str
) -> list[Turn]:
    """Load the pipeline, run it over the array, and read the turns back out."""
    # Same ordering rule as `transcribe.load_model`: torch cleanly first, before
    # anything else can import it halfway through its own import.
    import torch
    from pyannote.audio import Pipeline

    log.info("loading %s on %s", model, device)
    loading = time.monotonic()
    pipeline = Pipeline.from_pretrained(model, token=token)
    if pipeline is None:
        # from_pretrained returns None rather than raising when the checkpoint
        # cannot be reached — an unaccepted gated repository, most often.
        raise RuntimeError(f"could not load {model}; check the token and the repo conditions")
    log.info("loaded %s in %.1fs", model, time.monotonic() - loading)

    try:
        pipeline.to(torch.device(device))
        # A (channel, time) tensor, not a path: that is what keeps torchcodec's
        # FFmpeg — blocked here by Smart App Control — out of the picture.
        waveform = torch.from_numpy(np.ascontiguousarray(audio)).unsqueeze(0)
        result = pipeline({"waveform": waveform, "sample_rate": sample_rate})
        return _turns_of(result)
    finally:
        # Dropped rather than held warm, for the same reason the Whisper model
        # is: meetings are minutes apart at best, and the VRAM is worth more.
        del pipeline


def _turns_of(result: Any) -> list[Turn]:
    """The speaker turns of a pipeline result, across pyannote's output shapes.

    `pyannote.audio` 4 returns a `DiarizeOutput` dataclass carrying both an
    overlapping `speaker_diarization` and an `exclusive_speaker_diarization`,
    the latter documented as the one "adapted to downstream transcription" —
    which is precisely this. A checkpoint configured `legacy` returns a bare
    `Annotation` instead, as all of 3.x did.
    """
    annotation = getattr(result, "exclusive_speaker_diarization", None)
    if annotation is None:
        annotation = getattr(result, "speaker_diarization", result)
    return [
        Turn(start=float(segment.start), end=float(segment.end), speaker=str(speaker))
        for segment, _track, speaker in annotation.itertracks(yield_label=True)
    ]


# --- Aligning turns to segments ---------------------------------------------


def assign(
    segments: list[Segment], turns: list[Turn], fallback: str
) -> tuple[list[Segment], list[str]]:
    """Label each transcribed segment with the speaker who talked through most of it.

    Returns the relabeled segments and the speakers that actually appear, in
    order of first appearance.

    Alignment is by overlap and nothing else. Whisper and pyannote cut the
    channel at different places and neither is authoritative, so a segment goes
    to whichever speaker holds the most of its duration; a segment that no turn
    overlaps at all keeps `fallback` — `REMOTE`, the honest answer, rather than
    the nearest speaker's name.

    The labels are **renumbered by first appearance** to `SPEAKER_01`,
    `SPEAKER_02`, ... pyannote's own are zero-based and ordered by nothing in
    particular, and the folder contract promises one-based labels that read down
    the transcript in order. The numbering is per meeting and means nothing
    across meetings — the same person is a different number next week.
    """
    if not turns:
        return segments, []

    ordered = sorted(turns, key=lambda t: t.start)
    renamed: dict[str, str] = {}
    labeled: list[Segment] = []
    for segment in segments:
        raw = _dominant_speaker(segment.start, segment.end, ordered)
        if raw is None:
            labeled.append(replace(segment, speaker=fallback))
            continue
        if raw not in renamed:
            renamed[raw] = f"{SPEAKER_PREFIX}{len(renamed) + 1:02d}"
        labeled.append(replace(segment, speaker=renamed[raw]))
    return labeled, list(renamed.values())


def _dominant_speaker(start: float, end: float, turns: list[Turn]) -> str | None:
    """The speaker overlapping `start`..`end` most, or None when none does.

    Linear over the turns. An hour of meeting is a few thousand of each, so the
    quadratic pass costs milliseconds and buys code you can read.
    """
    totals: dict[str, float] = {}
    for turn in turns:
        if turn.start >= end:
            break  # Sorted by start: nothing later can overlap either.
        overlap = min(end, turn.end) - max(start, turn.start)
        if overlap > 0:
            totals[turn.speaker] = totals.get(turn.speaker, 0.0) + overlap
    if not totals:
        return None
    # Ties broken by speaker name, so the same input always labels the same way.
    return max(sorted(totals), key=lambda s: totals[s])
