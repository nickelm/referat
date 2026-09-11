r"""Splitting a channel into speakers, with `pyannote.audio`.

Both of them. `system.wav` carries everyone on a remote call in a single mono
stream, and `mic.wav` carries everyone **in the room** — Referat is mostly used
for meetings held in person, where one microphone hears the lot. This module says
who spoke when in a channel, so :mod:`referat.merge` can label the lines
`SPEAKER_01`, `SPEAKER_02`, ... instead of an undifferentiated `REMOTE` or a
`ME` that quietly swallows everybody who was sitting at the same table.

The numbering runs across the *meeting* rather than per channel — see
:func:`assign`'s `start` argument — because the same transcript carries both.

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

That is half of the torchcodec problem, and for a long time this module claimed
it was all of it. Pyannote imports `torchcodec` at module scope whether or not
it will ever decode with it, and that import alone loads the blocked DLLs — one
Windows Security notification each. :func:`_neutralize_torchcodec` handles that
half: never decoding is what makes torchcodec unnecessary, never importing it is
what makes it quiet.

**Imports are lazy**, as in :mod:`referat.transcribe`: `torch` and
`pyannote.audio` live behind the `transcribe` extra, and importing this module
must stay free for a base install.
"""

from __future__ import annotations

import logging
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from types import ModuleType
from typing import TYPE_CHECKING, Any

import numpy as np

from referat import gpu, sac
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

BLOCK_RETRIES = 2
BLOCK_RETRY_SECONDS = 30.0
"""How :func:`diarize` waits out a Smart App Control block, and how briefly.

A block is a window and not a verdict — see :mod:`referat.sac` — so the first
refusal is retried, but only twice and only half a minute apart, because the
window is measured in hours and this runs on the thread holding the GPU. The
long wait belongs to the tray, which re-probes on a timer and re-runs the
meeting from the audio :func:`referat.transcribe.audio_is_clean` kept. Only a
block is retried: a gated repository or an out-of-memory does not come right
by waiting, and retrying those would cost a minute per channel for nothing.
"""


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
    blocked: str = ""
    """The native module Smart App Control refused, when that is what failed.

    Kept apart from `reason` because the tray reads it: a failure that is a
    block is one the audio was kept for and a later run will recover, and a
    failure that is anything else is not.
    """
    model: str = ""
    device: str = ""
    seconds: float = 0.0
    turns: list[Turn] = field(default_factory=list)
    embeddings: dict[str, np.ndarray] = field(default_factory=dict)
    """One clustering centroid per speaker, keyed by *pyannote's* own label.

    The raw material :mod:`referat.voices` matches against. Not written to
    `meta.json` from here: the vectors belong in the channel's `speakers` block,
    beside the snippets they go with, and only after :func:`assign` has renumbered
    the labels they are keyed by.
    """

    @property
    def ok(self) -> bool:
        return self.status == DONE

    def to_json(self) -> dict[str, Any]:
        meta: dict[str, Any] = {"status": self.status}
        if self.reason:
            meta["reason"] = self.reason
        if self.blocked:
            meta["blocked"] = self.blocked
        if self.model:
            meta["model"] = self.model
            meta["device"] = self.device
            meta["seconds"] = round(self.seconds, 2)
            meta["turns"] = len(self.turns)
        return meta


# --- Running the pipeline ---------------------------------------------------


def diarize(
    audio: np.ndarray,
    sample_rate: int,
    config: Config,
    device: str,
    on_wait: Callable[[str], None] | None = None,
) -> Diarization:
    """Who spoke when in `audio`, as a list of :class:`Turn`.

    `audio` is mono float32, the shape :func:`referat.transcribe.decode_wav`
    returns; `device` is the one the transcription backend resolved to, so
    diarization follows Whisper onto the GPU or onto the CPU rather than
    deciding for itself. `on_wait` is told, in a sentence, when this is
    sitting out a Smart App Control block — the pipeline hands it to
    :mod:`referat.progress` so the window says so rather than *finding the
    speakers* for a minute.

    Never raises. A run that could not happen comes back with `status`
    :data:`SKIPPED` or :data:`FAILED` and a reason, and the caller keeps its
    undifferentiated `REMOTE` labels — and, when the failure was a block,
    keeps the audio too, on `blocked`'s say-so.
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
    attempt = 0
    while True:
        try:
            turns, embeddings = _run(audio, sample_rate, model, token, device)
            break
        except Exception as exc:
            # Deliberately bare. Anything at all going wrong here — a gated
            # repository, a network failure, an out-of-memory, a pyannote release
            # that renamed something — costs the speaker labels and stops there.
            blocked = sac.blocked_module(exc)
            if blocked and attempt < BLOCK_RETRIES:
                attempt += 1
                phrase = (
                    f"Smart App Control blocked {blocked}; retrying diarization in "
                    f"{BLOCK_RETRY_SECONDS:.0f}s ({attempt} of {BLOCK_RETRIES})"
                )
                log.warning("%s", phrase)
                if on_wait is not None:
                    on_wait(phrase)
                time.sleep(BLOCK_RETRY_SECONDS)
                continue
            log.exception("diarization failed")
            return Diarization(
                status=FAILED,
                reason=_reason(exc),
                blocked=blocked,
                model=model,
                device=device,
                seconds=time.monotonic() - started,
            )

    elapsed = time.monotonic() - started
    speakers = sorted({t.speaker for t in turns})
    log.info(
        "diarized %.1fs of audio in %.1fs: %d turns, %d speaker(s), %d embedding(s) on %s",
        audio.size / sample_rate if sample_rate else 0.0,
        elapsed,
        len(turns),
        len(speakers),
        len(embeddings),
        device,
    )
    return Diarization(
        status=DONE,
        model=model,
        device=device,
        seconds=elapsed,
        turns=turns,
        embeddings=embeddings,
    )


def _reason(exc: Exception) -> str:
    """One readable line naming what went wrong. See :data:`MAX_REASON`."""
    text = " ".join(f"{type(exc).__name__}: {exc}".split())
    return text if len(text) <= MAX_REASON else text[: MAX_REASON - 1].rstrip() + "…"


def _neutralize_torchcodec() -> None:
    """Stop pyannote's import of `torchcodec` from loading its FFmpeg DLLs.

    `pyannote.audio.core.io` imports `torchcodec` at module scope, inside a
    `try`, purely for the decoder it reaches for when handed a *path*. Referat
    never hands it a path — see :func:`_run` — so the decoder is dead weight.
    The import is not free, though. `torchcodec._core` loads
    `libtorchcodec_core{N}.dll`, probing six FFmpeg major versions in turn, and
    **Smart App Control is enforcing on this machine**, so each one is refused
    and each refusal raises a Windows Security notification. pyannote catches
    the failure and carries on with `TORCHCODEC_AVAILABLE = False`, which is the
    right outcome; the notifications are just what it cost to get there.

    A stub in `sys.modules` reaches the same outcome without touching a DLL.
    Dunder lookups answer `AttributeError`, so `hasattr` probes are merely
    False, and everything else raises `ImportError` — which is what pyannote's
    `except Exception` catches, on the first line that tries to use the module.

    Unlike its sibling :func:`referat.transcribe._neutralize_pyav` this does
    **not** try the real import first. There, the attempt is the diagnosis and
    costs one failed load; here the attempt *is* the problem. Nothing in Referat
    wants torchcodec even on a machine where it loads cleanly, because the
    pipeline is always handed a waveform, so there is nothing to lose by never
    asking and no configuration knob worth adding. Delete the two calls to get
    the real import back.

    The stub is duplicated rather than shared with `transcribe`: this module
    must not import that one — the dependency runs the other way, see the module
    docstring — and a third module existing only to hold eight lines would be
    worse than the eight lines.
    """
    if "torchcodec" in sys.modules:
        return

    class _Absent(ModuleType):
        def __getattr__(self, name: str) -> Any:
            if name.startswith("__") and name.endswith("__"):
                # A probe, not a use. Answer it the way a module without the
                # attribute would, so `hasattr` is False and nothing explodes.
                raise AttributeError(name)
            raise ImportError(
                f"torchcodec is not usable on this machine, so torchcodec.{name} "
                "cannot be reached. Referat hands pyannote a decoded waveform "
                "instead; see referat.diarize._run."
            )

    sys.modules["torchcodec"] = _Absent("torchcodec")
    log.info("stubbed torchcodec out; pyannote is handed a decoded waveform instead")


def _run(
    audio: np.ndarray, sample_rate: int, model: str, token: str, device: str
) -> tuple[list[Turn], dict[str, np.ndarray]]:
    """Load the pipeline, run it over the array, and read the result back out."""
    # Same ordering rule as `transcribe.load_model`: torch cleanly first, before
    # anything else can import it halfway through its own import.
    import torch

    # Before pyannote, not after: importing it is what probes for torchcodec.
    _neutralize_torchcodec()
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
        # A (channel, time) tensor, not a path: that is what keeps pyannote from
        # ever *decoding* with torchcodec's FFmpeg, blocked here by Smart App
        # Control. `_neutralize_torchcodec` above is what keeps it from *loading*
        # it; the two are separate halves and both are needed.
        waveform = torch.from_numpy(np.ascontiguousarray(audio)).unsqueeze(0)
        result = pipeline({"waveform": waveform, "sample_rate": sample_rate})
        turns, embeddings = _read_output(result)
        # Read out and dropped here rather than left to the return statement:
        # pyannote's output holds GPU tensors of its own, and a `result` still
        # alive in this frame is memory `gpu.release` below cannot reclaim.
        del result
        return turns, embeddings
    finally:
        # Dropped rather than held warm, for the same reason the Whisper model
        # is: meetings are minutes apart at best, and the VRAM is worth more.
        # `del` alone never achieved that -- see :mod:`referat.gpu`, which is the
        # half that hands the arena back to the driver.
        del pipeline
        gpu.release("diarizing")


def _read_output(result: Any) -> tuple[list[Turn], dict[str, np.ndarray]]:
    """The speaker turns and per-speaker embeddings, across pyannote's output shapes.

    `pyannote.audio` 4 returns a `DiarizeOutput` dataclass carrying both an
    overlapping `speaker_diarization` and an `exclusive_speaker_diarization`,
    the latter documented as the one "adapted to downstream transcription" —
    which is precisely this. A checkpoint configured `legacy` returns a bare
    `Annotation` instead, as all of 3.x did, and no embeddings with it.
    """
    annotation = getattr(result, "exclusive_speaker_diarization", None)
    if annotation is None:
        annotation = getattr(result, "speaker_diarization", result)
    turns = [
        Turn(start=float(segment.start), end=float(segment.end), speaker=str(speaker))
        for segment, _track, speaker in annotation.itertracks(yield_label=True)
    ]
    return turns, _embeddings_of(result)


def _embeddings_of(result: Any) -> dict[str, np.ndarray]:
    """One clustering centroid per speaker, keyed by pyannote's label. Empty on doubt.

    Three traps live in these five lines, and each of them files a voice under
    somebody else's name if it is missed.

    The rows are ordered by **`speaker_diarization.labels()`**, while the turns
    above are read out of `exclusive_speaker_diarization`. That is safe — pyannote
    applies the same rename mapping to both annotations — but it means the order
    has to come from `speaker_diarization` and from nowhere else.

    The array may be `None`, which is what `OracleClustering` returns, and it may
    be **zero-padded** when the clustering produced fewer centroids than the
    annotation has labels. A zero-norm row is not an embedding; it is filler, and
    filing it under a name would make every later meeting match against noise.

    `exclusive_speaker_diarization` can also hold *fewer* labels than
    `speaker_diarization` — a speaker only ever heard talking over somebody else.
    Such a label simply never reaches :func:`assign` and its embedding is dropped
    there rather than here.
    """
    embeddings = getattr(result, "speaker_embeddings", None)
    source = getattr(result, "speaker_diarization", None)
    if embeddings is None or source is None:
        return {}
    try:
        rows = np.asarray(embeddings, dtype=np.float32)
        labels = [str(label) for label in source.labels()]
    except Exception:
        log.warning("could not read the speaker embeddings back", exc_info=True)
        return {}
    if rows.ndim != 2:
        return {}

    kept: dict[str, np.ndarray] = {}
    for label, row in zip(labels, rows):
        if float(np.linalg.norm(row)) <= 0.0 or not np.isfinite(row).all():
            log.info("dropping the padding embedding pyannote returned for %s", label)
            continue
        kept[label] = row
    if len(labels) != rows.shape[0]:
        log.info(
            "pyannote returned %d embedding(s) for %d label(s); using the ones that line up",
            rows.shape[0],
            len(labels),
        )
    return kept


def _embed(
    clips: list[np.ndarray], sample_rate: int, model: str, token: str, device: str
) -> np.ndarray | None:
    """The body of :func:`embed`, free to raise into its handler."""
    import torch

    # Here too, and not only in `_run`: this path can reach pyannote without any
    # Whisper model having been loaded first, so it cannot lean on that one.
    _neutralize_torchcodec()
    from pyannote.audio import Pipeline

    pipeline = Pipeline.from_pretrained(model, token=token)
    if pipeline is None:
        raise RuntimeError(f"could not load {model}; check the token and the repo conditions")
    try:
        pipeline.to(torch.device(device))
        embedder = pipeline._embedding
        if embedder.sample_rate != sample_rate:
            # Not resampled here on purpose: every caller already hands over
            # 16 kHz audio, and silently feeding a model the wrong rate would
            # produce a plausible embedding of the wrong voice.
            log.warning(
                "the embedding model wants %d Hz and the clips are %d Hz; skipping",
                embedder.sample_rate,
                sample_rate,
            )
            return None

        floor = getattr(embedder, "min_num_samples", 0)
        vectors = []
        for clip in clips:
            if clip.size < floor:
                continue
            waveform = torch.from_numpy(np.ascontiguousarray(clip)).reshape(1, 1, -1)
            vector = np.asarray(embedder(waveform), dtype=np.float32).reshape(-1)
            norm = float(np.linalg.norm(vector))
            if norm > 0.0 and np.isfinite(vector).all():
                # Normalized before averaging, so a loud clip cannot outvote a
                # quiet one: only the direction carries the identity.
                vectors.append(vector / norm)
        if not vectors:
            return None
        return np.mean(vectors, axis=0).astype(np.float32)
    finally:
        # Dropped rather than held warm, for the same reason `_run` drops it, and
        # given back to the driver for the same reason `_run` gives it back.
        del pipeline
        gpu.release("embedding")


# --- Aligning turns to segments ---------------------------------------------


def assign(
    segments: list[Segment], turns: list[Turn], fallback: str, start: int = 1
) -> tuple[list[Segment], list[str], dict[str, str]]:
    """Label each transcribed segment with the speaker who talked through most of it.

    Returns the relabeled segments, the speakers that actually appear in order of
    first appearance, and the map from pyannote's own label to the `SPEAKER_NN`
    it became. That last one is what lets :mod:`referat.voices` permute the
    embeddings with the labels — pyannote orders them by *its* names, and
    renumbering one without the other files every voice under somebody else.

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

    `start` is the first number to hand out, because **both channels are diarized
    now** and the numbering is per *meeting*, not per channel: the microphone
    picks up everybody in the room, so it is clustered too, and a second channel
    starting again at `SPEAKER_01` would give two different people the same label
    in one transcript. The caller passes the count already used.
    """
    if not turns:
        return segments, [], {}

    ordered = sorted(turns, key=lambda t: t.start)
    renamed: dict[str, str] = {}
    labeled: list[Segment] = []
    for segment in segments:
        raw = _dominant_speaker(segment.start, segment.end, ordered)
        if raw is None:
            labeled.append(replace(segment, speaker=fallback))
            continue
        if raw not in renamed:
            renamed[raw] = f"{SPEAKER_PREFIX}{start + len(renamed):02d}"
        labeled.append(replace(segment, speaker=renamed[raw]))
    return labeled, list(renamed.values()), renamed


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
