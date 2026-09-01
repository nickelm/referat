r"""Turning a meeting's WAV files into `transcript.md`, with `faster-whisper`.

Runs on the GPU when there is one and degrades to the CPU when there is not,
because a transcript produced slowly beats no transcript at all. Everything here
is called from a background thread owned by :mod:`referat.tray`, and at build
step 8 from `referat rerun`, so nothing in this module touches the state machine
or the UI: it takes a :class:`referat.meeting.Meeting`, works on the files in its
folder, and writes the results back into `meta.json`.

**Both channels are transcribed, and both are diarized.** The microphone is not
one speaker by definition — Referat is mostly used for meetings held in person,
where the whole room arrives through it — so :mod:`referat.diarize` splits each
channel into `SPEAKER_01`, `SPEAKER_02`, ..., numbered across the *meeting* rather
than the channel, and :mod:`referat.voices` puts names on the ones it recognises.
A channel diarization could not split falls back to an undifferentiated
:data:`ME_LABEL` or :data:`REMOTE_LABEL`, which mean *this channel recorded it and
nothing attributed it* and never mean a particular person. Ordering the two into
one document belongs to :mod:`referat.merge`.

**Imports are lazy.** `faster_whisper` and `torch` live behind the `transcribe`
extra, ~3 GB of wheels a base install does not have, so they are imported inside
the functions that need them, exactly as :mod:`referat.recorder` does with
`sounddevice` and `pyaudiowpatch`. Importing this module must stay free.

**Audio is decoded here, not by faster-whisper.** See :func:`decode_wav` and
:func:`_neutralize_pyav`. It is also **resampled here**, in numpy alone: see
:func:`_resample`. Both are the same decision twice — the path from a WAV to a
transcript may not depend on a wheel's unsigned native code, because Smart App
Control on this machine blocks that at whatever moment it feels like it.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import re
import sys
import threading
import time
import wave
from dataclasses import dataclass, field, replace
from functools import lru_cache
from math import gcd
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np

from referat import diarize, index, merge, paths, voices
from referat.config import Config
from referat.meeting import Meeting, MeetingStatus

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
"""What Whisper wants, and what `mic.wav` is already recorded at."""

SAMPLE_WIDTH = 2
"""Both channels are 16-bit PCM, as written by :mod:`referat.recorder`."""

ME_LABEL = "ME"
"""Microphone speech that nothing attributed: the mic channel's fallback label.

Not the owner, which is what this used to mean, and not a synonym for "whoever
was recognised". The owner is identified exactly as everybody else is — by
matching a voiceprint — and is then written by **name**, like Anna or Mohammad.
Rendering them as `ME` instead put one person under two labels in a single
transcript, since :func:`referat.label.apply_name` spells the name out and the
pipeline did not: 2026-09-01_2102 came out with 178 `ME:` lines and 4 `Niklas:`
lines for the same voice.

So this label now means one thing and says something true whenever it appears:
the microphone recorded this and nothing put a name on it — diarization did not
run on the channel, or no turn overlapped the segment. Referat is primarily for
meetings held **in person**, where the room's other voices arrive through the
same microphone as the owner's, so it must not be widened back into a name: a
mic channel of unattributed speech is frequently two people, and spelling a name
onto it is the same failure as putting a name on a `SPEAKER_NN`.
"""

REMOTE_LABEL = "REMOTE"
"""The same, on the loopback channel: speech nothing attributed."""

DECODE_CHUNK_SECONDS = 60.0
"""How much of a resampled WAV is held as float32 at once. See :func:`decode_wav`."""

RESAMPLE_MARGIN = 1024
"""Overlap-save margin for chunked resampling, in `down`-sized input steps.

The resampling kernel reaches :data:`RESAMPLE_TAP_DENSITY` input samples either
side of each output sample — 30 for the 48 kHz -> 16 kHz case — so a thousand-odd
steps of history on each side is enormous overkill, which is the point: the seams
have to be inaudible and this costs 64 ms of redundant filtering per chunk.
"""

RESAMPLE_TAP_DENSITY = 10
"""Kernel half-width, in samples of whichever rate is higher.

`scipy.signal.resample_poly`'s own default, kept deliberately: it is what the
48 kHz loopback was resampled with until Smart App Control blocked scipy, and
matching it means a `referat rerun` of an old meeting decodes to the same samples
it did the first time. Measured against `resample_poly` on a real `system.wav`,
:func:`_resample` agrees to 138 dB — which is float rounding — and puts an 11 kHz
tone through at the same -67.6 dBFS.
"""

RESAMPLE_KAISER_BETA = 5.0
"""Kaiser window parameter, also `resample_poly`'s default. See above."""

RESAMPLE_HALF_TAPS_RANGE = (8, 128)
"""Clamp on the kernel half-width.

The floor keeps a near-unity ratio from being interpolated with three taps; the
ceiling bounds the cost of a pathological one, since every output sample pays for
every tap. Neither has ever bitten: the only ratios this machine produces are
48 kHz and 44.1 kHz down to 16 kHz, at 30 and 28 taps.
"""

RESAMPLE_OUT_BLOCK = 1 << 16
"""Output samples gathered at once, so the `(block, taps)` temporary stays ~8 MB.

Without it a 60-second chunk of 48 kHz would build a 960k x 61 float array —
230 MB, three times over — which is precisely the peak
:data:`DECODE_CHUNK_SECONDS` exists to avoid.
"""

# --- Quality thresholds -----------------------------------------------------
#
# What "the transcript came out fine" means, and therefore when the audio may be
# deleted. All three are faster-whisper's own signals at its own cut-offs:
# avg_logprob is the model's confidence, compression_ratio catches the repetition
# loops Whisper falls into on unintelligible input, and no_speech_prob catches a
# channel that is mostly not speech at all. Erring towards keeping the audio is
# the cheap mistake here; deleting a recording is the expensive one.

MIN_AVG_LOGPROB = -1.0
MAX_COMPRESSION_RATIO = 2.4
MAX_NO_SPEECH_PROB = 0.6

MIN_VOICED_SECONDS = 1.0
"""Less voiced audio than this in a whole channel means there was no speech in it.

Zero segments normally means "keep the audio" — a channel that transcribed to
nothing is exactly the one worth listening to. But a meeting held in person or
over a phone leaves a `system.wav` that is silence and the odd Windows
notification chime, and without an escape hatch that channel would block
:func:`release_audio_if_clean` forever and the meetings folder would never
reclaim a byte.

The measure is faster-whisper's own `duration_after_vad`: how much of the file
its voice-activity detector considered voice at all, before any transcription
happened. That is the right question — amplitude is not, since a single chime
is loud and says nothing about whether anyone spoke.
"""

VAD_FILTER = True
"""Both channels are padded with real silence, so voice activity detection earns
its keep — and faster-whisper still reports segment times in original audio
seconds, which is what the timestamps have to mean."""

_RUN_LOCK = threading.Lock()
"""One transcription at a time, process-wide.

A new meeting may start while the previous one is still transcribing —
`TRANSCRIBING -> RECORDING` is a legal edge and jobs are counted, not blocked —
so two jobs really can overlap. Two large-v3 models would not fit in 12 GB of
VRAM together, and even on the CPU they would only slow each other down. Jobs
queue here; recording is never what waits.
"""

_dll_dirs: list[Any] = []
"""Held deliberately: an `os.add_dll_directory` handle removes the directory
again when it is garbage collected."""


class TranscriptionError(RuntimeError):
    """Raised when a meeting could not be transcribed at all."""


class DecodeError(TranscriptionError):
    """Raised when a WAV could not be turned into samples.

    Its own class so that :func:`transcribe_meeting` does not answer it by
    re-running the whole meeting on the CPU. Decoding happens before any model is
    loaded and touches no device at all, so a CUDA attempt that died here would
    fail identically on the second pass — at the cost of several minutes and a
    duplicated traceback that hides the real cause in the middle of it.
    """


# --- What came back ---------------------------------------------------------


@dataclass(frozen=True)
class Segment:
    """One utterance, at a position in the channel's WAV file.

    `start` and `end` are seconds into the file, which is audio time with pauses
    already excluded — the recorder simply stops appending samples while paused.
    That is exactly what the transcript's timestamps mean, so nothing needs
    converting anywhere.
    """

    start: float
    end: float
    text: str
    avg_logprob: float
    compression_ratio: float
    no_speech_prob: float
    speaker: str = ""
    """Who said it, once :func:`referat.diarize.assign` has filled it in.

    Empty until then, and empty forever on the mic channel and on any run where
    diarization was skipped or failed. :func:`referat.merge.merge` falls back to
    the channel's own label when it is.
    """


@dataclass(frozen=True)
class Quality:
    """Whether a channel's transcript is trustworthy enough to delete its audio."""

    segments: int
    speech_seconds: float
    avg_logprob: float
    compression_ratio: float
    no_speech_prob: float
    voiced_seconds: float
    """How much of the channel the VAD took for voice. See :data:`MIN_VOICED_SECONDS`."""
    peak: float
    """Loudest sample in the channel, 0.0 to 1.0. Reported, not judged on."""

    @property
    def silent(self) -> bool:
        return self.voiced_seconds < MIN_VOICED_SECONDS

    @property
    def clean(self) -> bool:
        if self.segments == 0:
            # Nothing came back. That is fine only when there was nothing there:
            # a silent loopback channel has no audio worth keeping, while a loud
            # one that transcribed to nothing is precisely the case that does.
            return self.silent
        return (
            self.avg_logprob >= MIN_AVG_LOGPROB
            and self.compression_ratio <= MAX_COMPRESSION_RATIO
            and self.no_speech_prob <= MAX_NO_SPEECH_PROB
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "segments": self.segments,
            "speech_seconds": round(self.speech_seconds, 2),
            "avg_logprob": round(self.avg_logprob, 3),
            "compression_ratio": round(self.compression_ratio, 3),
            "no_speech_prob": round(self.no_speech_prob, 3),
            "voiced_seconds": round(self.voiced_seconds, 2),
            "peak": round(self.peak, 5),
            "silent": self.silent,
            "clean": self.clean,
        }


def score(segments: list[Segment], voiced_seconds: float, peak: float) -> Quality:
    """Aggregate per-segment metrics into one verdict for the channel.

    `avg_logprob` and `no_speech_prob` are weighted by segment duration, so a
    half-second interjection cannot outvote two minutes of clean speech.
    `compression_ratio` takes the worst segment instead of the mean: one runaway
    repetition loop is enough to distrust the transcript.

    `voiced_seconds` and `peak` are carried through rather than derived; the
    first decides whether an empty channel is empty because nothing was said —
    see :attr:`Quality.silent` — and the second is there to be read in
    `meta.json` when you want to know why a channel was kept.
    """
    if not segments:
        return Quality(0, 0.0, 0.0, 0.0, 1.0, voiced_seconds, peak)
    weights = [max(1e-3, s.end - s.start) for s in segments]
    total = sum(weights)
    return Quality(
        segments=len(segments),
        speech_seconds=total,
        avg_logprob=sum(s.avg_logprob * w for s, w in zip(segments, weights)) / total,
        compression_ratio=max(s.compression_ratio for s in segments),
        no_speech_prob=sum(s.no_speech_prob * w for s, w in zip(segments, weights)) / total,
        voiced_seconds=voiced_seconds,
        peak=peak,
    )


@dataclass
class ChannelTranscript:
    """One channel's segments, its speaker label, and how well it went."""

    channel: str
    label: str
    voiced_seconds: float
    peak: float
    segments: list[Segment] = field(default_factory=list)
    language: str = ""
    speakers: dict[str, voices.Cluster] = field(default_factory=dict)
    """The `SPEAKER_NN` diarization found here, each with the embedding that
    identifies it and the snippets `referat label` will play. Empty on the mic
    channel and on any undiarized run."""
    diarization: diarize.Diarization | None = None
    """What the diarizer did, or None when it was never asked — the mic channel."""

    @property
    def quality(self) -> Quality:
        return score(self.segments, self.voiced_seconds, self.peak)

    def to_json(self) -> dict[str, Any]:
        meta: dict[str, Any] = {
            "label": self.label,
            "language": self.language,
            **self.quality.to_json(),
        }
        if self.diarization is not None:
            meta["speakers"] = {
                label: cluster.to_json() for label, cluster in sorted(self.speakers.items())
            }
            meta["diarization"] = self.diarization.to_json()
        return meta

    @property
    def speaker_names(self) -> dict[str, str]:
        """The `SPEAKER_NN` to name mapping identification accepted on this channel."""
        return {label: c.name for label, c in self.speakers.items() if c.name}


# --- Reading the audio ------------------------------------------------------


def decode_wav(path: Path, target_rate: int = SAMPLE_RATE) -> np.ndarray:
    """One of Referat's WAVs as the float32 mono array Whisper wants.

    faster-whisper would happily open the file itself, but only through PyAV,
    which is deliberately bypassed — see :func:`_neutralize_pyav`. Doing it here
    is no loss: these are always Referat's own 16-bit PCM files, the stdlib
    `wave` module reads them exactly, and `system.wav` needs resampling from the
    output device's native mix rate regardless.

    `mic.wav` is already at 16 kHz and is read in one go. `system.wav` is not,
    and it is large: an hour is 345 MB on disk and 691 MB the moment it becomes
    float32, before the resampler allocates anything of its own. So it is decoded
    a minute at a time by :func:`_resample_stream`. Measured on a real 48 kHz
    `system.wav`, that takes the peak from 1.7 GB per meeting-hour down to about
    230 MB plus a fixed ~30 MB working set — which is to say down to the array
    Whisper needs anyway, and nothing else.
    """
    try:
        with wave.open(str(path), "rb") as wav:
            rate, channels, width = wav.getframerate(), wav.getnchannels(), wav.getsampwidth()
            frames = wav.getnframes()
            if width != SAMPLE_WIDTH:
                raise DecodeError(f"{path.name} is {width * 8}-bit, expected 16-bit PCM")
            if rate == target_rate:
                return _to_mono_float(wav.readframes(frames), channels)
            audio = _resample_stream(wav, frames, channels, rate, target_rate)
    except DecodeError:
        raise
    except Exception as exc:
        # Everything that reaches a WAV arrives as one class, so the caller can
        # tell "this file will not decode" from "the GPU died" without reading
        # the message. See :class:`DecodeError`.
        raise DecodeError(f"could not decode {path.name}: {exc}") from exc
    log.info("%s: resampled %d Hz -> %d Hz", path.name, rate, target_rate)
    return audio


def _to_mono_float(raw: bytes, channels: int) -> np.ndarray:
    """Interleaved 16-bit PCM to mono float32 in -1.0 .. 1.0."""
    samples = np.frombuffer(raw, dtype=np.int16)
    if channels > 1:  # Not written by this recorder, but cheap to survive.
        usable = (samples.size // channels) * channels
        samples = samples[:usable].reshape(-1, channels).mean(axis=1)
    audio = samples.astype(np.float32)
    audio /= 32768.0  # In place: a `/` here would allocate the array twice.
    return audio


@lru_cache(maxsize=4)
def _resample_kernel(up: int, down: int) -> tuple[np.ndarray, int]:
    """The polyphase kernel for one ratio: `(up, 2 * half)` taps, and `half`.

    One row per output phase. Output sample `m` lands at input time `m * down /
    up`, whose fractional part is `(m * down) % up / up` — so there are exactly
    `up` distinct fractional offsets however long the file is, and the whole
    filter is a table of `up` windowed sincs. That is the entire polyphase trick,
    and it is what keeps 44.1 kHz (`up = 160`) from costing 160 times anything.

    The window is Kaiser, evaluated at fractional positions through `np.i0`
    rather than taken from `np.kaiser`, which only samples it at integers. Rows
    are normalized to sum to one, which sets the DC gain to unity and quietly
    absorbs the asymmetry of a kernel that reaches one sample further right than
    left.

    Cached because it depends on nothing but the ratio, and
    :func:`_resample_stream` asks for it once per 60-second chunk.
    """
    low, high = RESAMPLE_HALF_TAPS_RANGE
    half = min(max(-(-RESAMPLE_TAP_DENSITY * max(up, down) // up), low), high)
    taps = np.arange(-half + 1, half + 1, dtype=np.float64)
    offsets = np.arange(up, dtype=np.float64) / up
    distance = taps[None, :] - offsets[:, None]
    # Relative to the *input* Nyquist: when downsampling, the passband has to end
    # at the output's Nyquist instead, or the decimation aliases it back in.
    cutoff = min(1.0, up / down)
    shape = np.clip(1.0 - (distance / half) ** 2, 0.0, None)
    window = np.i0(RESAMPLE_KAISER_BETA * np.sqrt(shape)) / np.i0(RESAMPLE_KAISER_BETA)
    kernel = np.sinc(distance * cutoff) * window
    kernel /= kernel.sum(axis=1, keepdims=True)
    return kernel.astype(np.float32), half


def _resample(block: np.ndarray, up: int, down: int) -> np.ndarray:
    """`scipy.signal.resample_poly(block, up, down)`, in numpy and the stdlib alone.

    **Why not scipy.** `from scipy.signal import resample_poly` pulls in
    `scipy.stats` and `scipy.integrate` behind it, which is some hundred unsigned
    `.pyd` files, and Smart App Control blocks an unsigned binary whose cloud
    reputation has not yet vouched for it. On 2026-09-01 it blocked three
    different ones inside five minutes — `_odepack`, `_stats_pythran`, `_sobol` —
    and then stopped, the reputation having arrived. So this was never a scipy
    that does not work: it is a scipy that stops working at an unpredictable
    moment, for minutes at a time, and the moment it picked was a `referat rerun`.

    A transcript may not depend on that. numpy is already unavoidable — nothing in
    this module runs without it — so a resampler written against numpy alone adds
    no new way to fail, while scipy on this path added a whole package of them.
    It is the same judgement as :func:`_neutralize_pyav` and
    :func:`referat.diarize._neutralize_torchcodec`, one layer down, and it is the
    project rule about keeping the import-critical path narrow rather than a new
    one.

    Contract-compatible with `resample_poly` on purpose, because meetings
    recorded before this existed have to re-run to the same samples: same output
    length, `ceil(n * up / down)`; same zero-phase alignment, output `m` at input
    time `m * down / up`; same zero padding past the ends; same default kernel.
    Verified against it at 138 dB on a real `system.wav` while it was importable.
    """
    kernel, half = _resample_kernel(up, down)
    count = -(-block.size * up // down)
    out = np.empty(count, dtype=np.float32)
    # Zero-padded rather than edge-clamped: `resample_poly` runs the filter off
    # the ends into silence, and only the file's true ends ever see this, since
    # the overlap-save margins hide every seam.
    padded = np.zeros(block.size + 2 * half, dtype=np.float32)
    padded[half : half + block.size] = block
    reach = np.arange(1, 2 * half + 1)  # tap offsets, already shifted past the pad

    for start in range(0, count, RESAMPLE_OUT_BLOCK):
        stop = min(start + RESAMPLE_OUT_BLOCK, count)
        position = np.arange(start, stop, dtype=np.int64) * down
        rows = padded[(position // up)[:, None] + reach[None, :]]
        # einsum rather than (rows * taps).sum(axis=1): one pass, no temporary
        # the size of the gather.
        out[start:stop] = np.einsum("ij,ij->i", rows, kernel[position % up])
    return out


def _resample_stream(
    wav: wave.Wave_read, frames: int, channels: int, rate: int, target_rate: int
) -> np.ndarray:
    """Resample a WAV to `target_rate` a chunk at a time, overlap-save.

    :func:`_resample` low-pass filters as it decimates; plain
    decimation would alias speech down into the band Whisper listens to. Applied
    per chunk it would also ring at every seam, so each chunk is filtered with
    :data:`RESAMPLE_MARGIN` steps of real audio on either side and the margins
    are then trimmed off the output. Only the file's own two ends keep the
    resampler's zero padding, which is where it belongs — the result matches
    resampling the whole array at once to within float rounding.

    Chunk and margin are whole multiples of `down`, so every trim is an exact
    number of output samples and the pieces abut with no drift.
    """
    divisor = gcd(rate, target_rate)
    up, down = target_rate // divisor, rate // divisor
    margin = RESAMPLE_MARGIN * down
    chunk = max(down, int(DECODE_CHUNK_SECONDS * rate) // down * down)

    out = np.empty(-(-frames * up // down) + up, dtype=np.float32)
    written = 0
    position = 0
    while position < frames:
        take = min(chunk, frames - position)
        left = min(margin, position)
        right = (min(margin, frames - position - take) // down) * down
        wav.setpos(position - left)
        block = _to_mono_float(wav.readframes(left + take + right), channels)
        if block.size == 0:  # A header claiming more frames than the file holds.
            break

        resampled = _resample(block, up, down)
        head, tail = left // down * up, right // down * up
        piece = resampled[head : resampled.size - tail] if tail else resampled[head:]
        out[written : written + piece.size] = piece
        written += piece.size
        position += take
    return out[:written]


def _neutralize_pyav() -> None:
    """Let `import faster_whisper` succeed on a machine where PyAV will not load.

    faster-whisper imports PyAV at module scope purely for :func:`decode_audio`,
    its media decoder. On this laptop that import fails outright: **Smart App
    Control is enforcing**, and PyAV's bundled FFmpeg DLLs are unsigned, so
    Windows blocks them. Turning Smart App Control off is a one-way system-wide
    change — Windows cannot re-enable it without a reinstall — and is not
    something a personal recorder should ask for.

    It is also unnecessary. `WhisperModel.transcribe()` skips `decode_audio`
    entirely when handed a numpy array, which :func:`decode_wav` produces, so
    PyAV is dead weight here: a bundled media decoder for files that are already
    raw PCM. A stub in `sys.modules` satisfies the import and nothing else. Any
    attribute access on it raises, loudly, so if a future faster-whisper really
    starts needing PyAV that shows up as an error rather than as wrong audio.

    The stub answers dunder lookups with `AttributeError` rather than
    `ImportError`, so `hasattr(av, "__file__")` is merely False. Import
    machinery and feature probes ask that question routinely — `pyannote.audio`
    does, on the way in — and an `ImportError` there is not a refusal to decode,
    it is a crash in code that was only looking.

    Does nothing when PyAV imports fine.
    :func:`referat.diarize._neutralize_torchcodec` is the same trick played on
    pyannote's bundled decoder, and differs in exactly one way: it never tries
    the real import, because for torchcodec the attempt is itself the problem.
    """
    if "av" in sys.modules:
        return
    try:
        import av  # noqa: F401

        return
    except ImportError as exc:
        log.info("PyAV is unavailable (%s); decoding WAVs directly instead", exc)

    class _Absent(ModuleType):
        def __getattr__(self, name: str) -> Any:
            if name.startswith("__") and name.endswith("__"):
                # A probe, not a use. Answer it the way a module without the
                # attribute would, so `hasattr` is False and nothing explodes.
                raise AttributeError(name)
            raise ImportError(
                f"PyAV is not usable on this machine, so av.{name} cannot be reached. "
                "Referat decodes its own WAV files; see referat.transcribe.decode_wav."
            )

    sys.modules["av"] = _Absent("av")


# --- Choosing a backend -----------------------------------------------------


@dataclass(frozen=True)
class Backend:
    """The model, device and precision one run will actually use."""

    model: str
    device: str
    compute_type: str

    def __str__(self) -> str:
        return f"{self.model} on {self.device} ({self.compute_type})"


def cpu_backend(config: Config) -> Backend:
    """The fallback: the smaller model, quantized, on the CPU.

    Falling back to the CPU also falls back to `cpu_fallback_model`. large-v3 on
    this machine's CPU would take longer than the meeting did.
    """
    t = config.transcription
    compute = t.compute_type if t.compute_type != "auto" else "int8"
    return Backend(model=t.cpu_fallback_model, device="cpu", compute_type=compute)


def resolve_backend(config: Config) -> Backend:
    """Pick the model, device and compute type from the config and the hardware."""
    t = config.transcription
    device = t.device
    if device == "auto":
        device = "cuda" if cuda_available() else "cpu"
        log.info("device auto-detected as %s", device)
    if device == "cpu":
        return cpu_backend(config)
    compute = t.compute_type if t.compute_type != "auto" else "float16"
    return Backend(model=t.model, device="cuda", compute_type=compute)


def cuda_available() -> bool:
    """Ask torch whether there is a usable GPU. False on any import trouble."""
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        log.info("torch reports no usable CUDA device", exc_info=True)
        return False


def ensure_cuda_dlls() -> None:
    """Put torch's bundled cuDNN and cuBLAS on the DLL search path.

    faster-whisper runs on CTranslate2, which links cuDNN 9 and cuBLAS but ships
    neither; on Windows that surfaces as a bare "library not found" the moment a
    CUDA model is constructed. The `cu128` torch wheel this project already pins
    carries both in `torch/lib`, so pointing the loader there is the whole fix.

    The handles are kept in a module-level list on purpose: letting one be
    collected removes the directory again.
    """
    if _dll_dirs:
        return
    try:
        import torch

        lib = Path(torch.__file__).resolve().parent / "lib"
        if lib.is_dir():
            _dll_dirs.append(os.add_dll_directory(str(lib)))
            log.debug("added %s to the DLL search path", lib)
    except Exception:
        # Not fatal: the DLLs may already be reachable. Let the model load fail
        # on its own terms, and let the CPU fallback catch it if it does.
        log.warning("could not add torch's lib directory to the DLL search path", exc_info=True)


def load_model(backend: Backend) -> Any:
    """Construct a `WhisperModel` for `backend`, downloading it on first use."""
    # Import torch first, on every device. faster-whisper pulls in ctranslate2,
    # whose converters import torch halfway through their own import, and torch
    # 2.11 does not survive being entered that way: it reaches
    # `torch.utils._debug_mode` before `torch.library` is bound and dies with a
    # circular-import AttributeError. Importing it cleanly at the top level first
    # makes that a no-op. Reproducible, and it would otherwise have broken every
    # CPU run, where nothing else touches torch.
    import torch  # noqa: F401

    _neutralize_pyav()
    if backend.device == "cuda":
        ensure_cuda_dlls()
    from faster_whisper import WhisperModel

    log.info("loading %s", backend)
    started = time.monotonic()
    model = WhisperModel(backend.model, device=backend.device, compute_type=backend.compute_type)
    log.info("loaded %s in %.1fs", backend.model, time.monotonic() - started)
    return model


# --- Transcribing -----------------------------------------------------------


def transcribe_channel(
    meeting: Meeting,
    path: Path,
    model: Any,
    config: Config,
    label: str,
    *,
    diarize_channel: bool = False,
    speaker_start: int = 1,
    device: str = "cpu",
) -> ChannelTranscript:
    """Run one WAV file through the model and collect its segments.

    `diarize_channel` additionally asks :mod:`referat.diarize` who spoke and
    :mod:`referat.voices` who they are, which **both** channels want: the
    microphone hears the whole room in a meeting held in person, so it is no more
    one speaker than the loopback is. `speaker_start` is the first `SPEAKER_NN`
    number free for this channel, since the numbering runs across the meeting
    rather than restarting per channel. `device` is the transcription
    backend's, so both follow Whisper onto the GPU or the CPU rather than
    choosing for themselves. `meeting` is here for the same reason — the speaker
    snippets are written into its folder.
    """
    language = config.transcription.language.strip() or None
    started = time.monotonic()
    # A decoded array, not a path: that is what keeps PyAV out of the picture.
    audio = decode_wav(path)
    # Two passes over the array rather than one over `np.abs(audio)`, which would
    # copy a quarter of a gigabyte to find a single number.
    peak = float(max(audio.max(initial=0.0), -audio.min(initial=0.0)))
    segments, info = model.transcribe(audio, language=language, vad_filter=VAD_FILTER)
    # How much of the file the VAD took for voice, before any transcription. A
    # channel with no segments *and* nothing voiced held no speech to lose; one
    # with voiced audio and no segments is exactly the case worth keeping. Fall
    # back to the whole duration if a future faster-whisper stops reporting it:
    # "assume somebody spoke" is the direction that keeps the audio.
    voiced = getattr(info, "duration_after_vad", None)
    voiced_seconds = float(voiced if voiced is not None else audio.size / SAMPLE_RATE)

    # The generator is where the work — and any CUDA failure — actually happens,
    # so it is drained here, inside the caller's try.
    collected = [
        Segment(
            start=float(s.start),
            end=float(s.end),
            text=text,
            avg_logprob=float(s.avg_logprob),
            compression_ratio=float(s.compression_ratio),
            no_speech_prob=float(s.no_speech_prob),
        )
        for s in segments
        if (text := " ".join(s.text.split()))
    ]
    transcript = ChannelTranscript(
        channel=path.stem,
        label=label,
        voiced_seconds=voiced_seconds,
        peak=peak,
        segments=collected,
        language=str(getattr(info, "language", "") or ""),
    )
    q = transcript.quality
    log.info(
        "%s: %d segments spanning %.1fs, %.1fs of it voiced, transcribed in "
        "%.1fs (logprob %.2f, compression %.2f, peak %.4f, silent=%s, clean=%s)",
        path.name,
        q.segments,
        q.speech_seconds,
        q.voiced_seconds,
        time.monotonic() - started,
        q.avg_logprob,
        q.compression_ratio,
        q.peak,
        q.silent,
        q.clean,
    )
    if diarize_channel:
        # On the array that is still in scope, so the WAV is decoded once.
        _diarize_into(meeting, transcript, audio, config, device, speaker_start)
    return transcript


def _diarize_into(
    meeting: Meeting,
    transcript: ChannelTranscript,
    audio: np.ndarray,
    config: Config,
    device: str,
    speaker_start: int = 1,
) -> None:
    """Split one channel into speakers and name the ones it can, in place. Never raises.

    One cheap refusal comes first: a channel that transcribed to nothing has no
    lines to label, so there is nothing for a pipeline to do but spend minutes
    finding that out. That is the ordinary state of `system.wav` for a meeting
    held in person, where the loopback records silence and the odd notification
    chime, so :attr:`Quality.silent` distinguishes the two reasons — an empty
    channel that held no speech, and an empty channel that held speech Whisper
    could not transcribe. The second is the one worth looking into.

    Everything after that is :func:`referat.diarize.diarize`'s problem, and it
    reports failure rather than raising: a diarization error must not reach
    :func:`transcribe_meeting`, which cannot tell one from a dying GPU and would
    re-transcribe the whole meeting on the CPU.
    :func:`referat.voices.identify` is held to the same contract for the same
    reason, so this function has no error handling of its own to do.
    """
    if not transcript.segments:
        transcript.diarization = diarize.Diarization(
            reason="no voice in the channel"
            if transcript.quality.silent
            else "nothing was transcribed"
        )
        return

    result = diarize.diarize(audio, SAMPLE_RATE, config, device)
    transcript.diarization = result
    if result.ok:
        transcript.segments, found, renaming = diarize.assign(
            transcript.segments, result.turns, transcript.label, speaker_start
        )
        log.info("%s: %s", transcript.channel, ", ".join(found) or "no speakers found")
        names = voices.identify(meeting, transcript, renaming, audio, SAMPLE_RATE, config)
        if names:
            # The label is metadata on the segment, so putting a name on it here
            # means `merge` and `render_transcript` need to know nothing about
            # identification at all: they already prefer a segment's own speaker.
            transcript.segments = [
                replace(segment, speaker=names.get(segment.speaker, segment.speaker))
                for segment in transcript.segments
            ]
    else:
        log.info(
            "%s: keeping %s labels, diarization %s (%s)",
            transcript.channel,
            transcript.label,
            result.status,
            result.reason,
        )


def transcribe_channels(
    meeting: Meeting, config: Config, backend: Backend, *, tolerate_failures: bool = False
) -> tuple[list[ChannelTranscript], dict[str, str]]:
    """Load one model and run both channels through it.

    Serialized process-wide, and the model is dropped as soon as the meeting is
    done rather than held warm: loading costs seconds against meetings measured
    in tens of minutes, and a resident large-v3 would occupy the GPU for the rest
    of the session.

    A channel whose file is missing is skipped — either device may have failed to
    open, and half a meeting is worth far more than none.

    `tolerate_failures` decides what a channel that *raises* costs. False, on the
    CUDA attempt, lets it propagate so :func:`transcribe_meeting` can retry the
    whole meeting on the CPU: an out-of-memory on the second channel is a GPU
    problem, not a channel problem. True, on the final attempt, keeps whatever
    did work and returns the errors alongside it, so a corrupt `system.wav`
    cannot cost you a perfectly good microphone transcript. Raises only when
    nothing at all came back.
    """
    # Both channels are diarized. The microphone used to be taken as one speaker
    # by definition -- the owner and nobody else -- which is true of a Zoom call
    # and false of the meetings this is mostly used for: in a room, everybody
    # goes through the one microphone, and that assumption merged a whole meeting
    # into `ME`. The mic goes first so its speakers number from SPEAKER_01 and
    # read down the transcript in order.
    channels = (
        (meeting.mic_path, ME_LABEL),
        (meeting.system_path, REMOTE_LABEL),
    )
    with _RUN_LOCK:
        model = load_model(backend)
        try:
            results: list[ChannelTranscript] = []
            errors: dict[str, str] = {}
            # Numbering is per meeting, not per channel: two channels each
            # starting at SPEAKER_01 would put two different people behind one
            # label in the same transcript.
            speakers_used = 0
            for path, label in channels:
                if not path.exists():
                    log.warning("no %s in %s", path.name, meeting.dir)
                    continue
                try:
                    transcript = transcribe_channel(
                        meeting,
                        path,
                        model,
                        config,
                        label,
                        diarize_channel=True,
                        speaker_start=speakers_used + 1,
                        device=backend.device,
                    )
                    speakers_used += len(transcript.speakers or {})
                    results.append(transcript)
                except Exception as exc:
                    if not tolerate_failures:
                        raise
                    log.exception("could not transcribe %s", path.name)
                    errors[path.stem] = str(exc)
            if errors and not results:
                raise TranscriptionError("; ".join(f"{k}: {v}" for k, v in errors.items()))
            return results, errors
        finally:
            del model


# --- Writing transcript.md --------------------------------------------------


def format_timestamp(seconds: float) -> str:
    """`HH:MM:SS`, elapsed since the start of the recording."""
    total = max(0, int(seconds))
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


ENTRY_SEPARATOR = "\n\n"
"""What goes between two entries of `transcript.md`: a blank line.

**A single newline is not enough, and that is a Markdown fact rather than a
matter of taste.** In CommonMark a newline inside a block is a *soft* break,
rendered as a space — so a transcript whose entries were separated by one
newline came out of every Markdown viewer as one unbroken paragraph, hundreds of
utterances long. The meetings folder opens Markdown rendered, which makes that
the normal way these files are read.

A blank line was chosen over the two alternatives. Two trailing spaces are the
other hard break, and they are two characters nobody can see, in a file plenty of
editors would strip them out of on save. Turning each entry into a `- ` list item
reads well and breaks both of the line-anchored patterns in :mod:`referat.label`,
which is a real cost for a cosmetic gain.

:func:`render_transcript` and :func:`reflow_transcript` share this, so the file
Referat writes and the file it repairs cannot come to disagree.
"""

ENTRY_RE = re.compile(r"^\[\d{2}:\d{2}:\d{2}\] [^:]+: ")
"""One rendered entry: `[HH:MM:SS] <label>: `.

Only :func:`reflow_transcript` uses it, and only to be sure that the two lines it
is about to put a blank line between really are two entries. Deliberately not
imported from :mod:`referat.label`, whose patterns anchor on a *known speaker* in
order to rewrite that field; this one asks the weaker question of whether a line
has the shape this module writes.
"""


def render_transcript(meeting: Meeting, entries: list[tuple[float, str, str]]) -> str:
    """The `transcript.md` document, from `(start, label, text)` in time order.

    Takes already-ordered entries rather than channels; ordering the channels is
    :func:`referat.merge.merge`'s job.

    Entries are separated by a blank line — see :data:`ENTRY_SEPARATOR` for why
    one newline was not enough.
    """
    when = meeting.started_at.strftime("%Y-%m-%d %H:%M")
    minutes = round(meeting.duration_seconds / 60)
    header = f"## Meeting {when} ({minutes} min)"
    body = [f"[{format_timestamp(start)}] {label}: {text}" for start, label, text in entries]
    return ENTRY_SEPARATOR.join([header, *body]) + "\n"


def reflow_transcript(text: str) -> tuple[str, int]:
    """Put a blank line between the entries of an already-written transcript.

    Every transcript recorded before :data:`ENTRY_SEPARATOR` existed has a single
    newline between its entries and renders as one paragraph. Most of them have
    had their audio released, so no `referat rerun` can ever regenerate them —
    `referat reflow` is the only way they become readable, and this is what it
    calls. Returns the new text and how many blank lines were inserted.

    **This is not the kind of edit the immutability rule forbids.** It inserts
    whitespace *between* lines and rewrites no line: no word of speech, no
    timestamp and no speaker label is touched, which is the same narrowness
    `referat label` observes when it rewrites the label field.

    Conservative on purpose: a blank line goes in only where **both** the line
    before and the line after match :data:`ENTRY_RE`. A file holding anything
    else — a hand-annotated transcript, a format from some later version — keeps
    that part exactly as it is rather than being reformatted on a guess.
    Idempotent, so a second run inserts nothing and the CLI writes no file.
    """
    out: list[str] = []
    inserted = 0
    for line in text.split("\n"):
        if out and ENTRY_RE.match(line) and ENTRY_RE.match(out[-1]):
            out.append("")
            inserted += 1
        out.append(line)
    return "\n".join(out), inserted


# --- Releasing the audio ----------------------------------------------------


def release_audio_if_clean(meeting: Meeting) -> bool:
    """Delete the WAVs once every channel has a transcript that looks trustworthy.

    Recording costs about 460 MB an hour, so something has to reclaim it — but
    only when the transcript is certainly good enough to stand in for the audio.
    The gate is *every* channel in `meta.json`'s `audio` block, not just the ones
    that happened to produce segments: a channel that was recorded but not
    transcribed keeps both files, and so does a channel whose transcript looks
    garbled, empty or uncertain. A channel with no voice in it at all counts as
    clean — see :data:`MIN_VOICED_SECONDS` — because an in-person meeting, where
    the loopback records nothing but silence and notification chimes, would
    otherwise pin every recording to the disk forever.

    **This function is the gate and nothing else.** The deletion itself is
    :func:`release_audio`, which `referat promote --release-audio` also calls when
    a person overrules this gate on a `gate_failed` meeting.
    """
    if meeting.status is not MeetingStatus.TRANSCRIBED:
        return False
    channels = meeting.transcription.get("channels") or {}
    present = sorted(meeting.audio)
    if not present:
        return False

    untranscribed = [name for name in present if name not in channels]
    if untranscribed:
        log.info(
            "keeping the audio of %s: %s not transcribed", meeting.id, ", ".join(untranscribed)
        )
        return False
    unclean = [name for name in present if not channels[name].get("clean")]
    if unclean:
        log.info("keeping the audio of %s: %s looks unreliable", meeting.id, ", ".join(unclean))
        return False

    return release_audio(meeting) >= 0


def release_audio(meeting: Meeting) -> int:
    """Delete this meeting's channel WAVs, unconditionally. Bytes freed, or -1.

    **The decision is the caller's; this is only the act.** Split out of
    :func:`release_audio_if_clean` at build step 15, when `referat promote
    --release-audio` became the second thing that deletes a meeting's audio: the
    sidebar's off-ramp for a gate-failed meeting, where the person looking at the
    transcript overrules the quality gate. Deleting the WAVs and recording that it
    was deliberate has to happen the same way both times, or `audio_released`
    would mean two different things.

    A channel whose file is already gone is not a failure — it is the state this
    function is trying to reach — so it is skipped rather than counted. Anything
    that actually refuses to be deleted returns -1 with the files left as they
    are: a half-released meeting must not be promoted, because the invariant that
    no WAV ever reaches the meetings folder is the whole reason for staging.

    The `audio` block itself stays — frames, duration and device remain on record
    — and `audio_released` says the files are gone deliberately. The `speakers/`
    snippets stay too: they are cut precisely because this deletes the audio they
    were cut from, and they go when their speaker is named.
    """
    freed = 0
    for name in sorted(meeting.audio):
        wav = meeting.dir / str(meeting.audio[name].file)
        try:
            size = wav.stat().st_size
        except OSError:
            continue
        try:
            wav.unlink()
        except OSError:
            log.exception("could not delete %s", wav)
            return -1
        freed += size
    meeting.transcription["audio_released"] = True
    meeting.save()
    log.info("released %.0f MB of audio from %s", freed / 1e6, meeting.id)
    return freed


# --- The job ----------------------------------------------------------------


def transcribe_meeting(meeting: Meeting, config: Config) -> Meeting:
    """Transcribe one meeting folder end to end and write everything back.

    The single entry point: the tray's background thread now, and `referat rerun`
    at build step 8. Raises :class:`TranscriptionError` after marking the meeting
    `failed`, so a folder is never left stuck at `transcribing` with nobody
    working on it.

    A run where one channel failed and another did not still writes
    `transcript.md` from the channel that worked, and still raises — the meeting
    really did fail, and both WAVs stay on disk for `referat rerun`, but there is
    something readable in the folder in the meantime.
    """
    log.info("transcribing %s", meeting.id)
    started = time.monotonic()
    meeting.status = MeetingStatus.TRANSCRIBING
    meeting.transcription = {"status": str(MeetingStatus.TRANSCRIBING)}
    meeting.save()

    backend = resolve_backend(config)
    try:
        try:
            transcripts, errors = transcribe_channels(
                meeting, config, backend, tolerate_failures=backend.device != "cuda"
            )
        except DecodeError:
            # Not a device problem, so the CPU would fail in exactly the same
            # place several minutes later. The retry below is for the GPU, and a
            # WAV that will not decode has not reached it yet.
            raise
        except Exception:
            if backend.device != "cuda":
                raise
            # The GPU is the fast path, not a requirement. Anything at all going
            # wrong on it — a missing DLL, an out-of-memory, a driver hiccup —
            # costs speed, never the transcript.
            log.exception("CUDA transcription failed; falling back to the CPU")
            backend = cpu_backend(config)
            transcripts, errors = transcribe_channels(
                meeting, config, backend, tolerate_failures=True
            )

        paths.write_text_atomic(
            meeting.transcript_path, render_transcript(meeting, merge.merge(transcripts))
        )
    except Exception as exc:
        meeting.status = MeetingStatus.FAILED
        meeting.transcription = _transcription_meta(
            backend, [], time.monotonic() - started, status=MeetingStatus.FAILED, error=str(exc)
        )
        meeting.save()
        log.exception("transcription of %s failed", meeting.id)
        raise TranscriptionError(f"could not transcribe {meeting.id}: {exc}") from exc

    elapsed = time.monotonic() - started
    status = MeetingStatus.FAILED if errors else MeetingStatus.TRANSCRIBED
    meeting.status = status
    meeting.transcription = _transcription_meta(backend, transcripts, elapsed, status=status)
    # Re-derived from this run rather than carried over: `referat rerun`
    # re-diarizes and renumbers from scratch, so last week's SPEAKER_02 is not
    # this run's. The names survive anyway, because they are looked up in the
    # database each time rather than remembered here.
    meeting.speaker_names = {
        label: name for t in transcripts for label, name in t.speaker_names.items()
    }
    if errors:
        meeting.transcription["errors"] = errors
    meeting.save()
    if errors:
        log.error(
            "transcribed only %s of %s; %s failed",
            ", ".join(t.channel for t in transcripts) or "nothing",
            meeting.id,
            ", ".join(errors),
        )
        raise TranscriptionError(
            f"could not transcribe every channel of {meeting.id}: "
            + "; ".join(f"{k}: {v}" for k, v in errors.items())
        )
    log.info("transcribed %s in %.1fs with %s", meeting.id, elapsed, backend)

    # Before the release, not after: this reads `mic.wav`, which the release
    # deletes. It writes only to the voices database, and it costs nothing when
    # it declines — which is every meeting held in person.
    voices.bootstrap_owner(meeting, transcripts, config)
    if release_audio_if_clean(meeting):
        promote_meeting(meeting, config)
    else:
        # The lifecycle value that used to be a three-way inference: status is
        # `transcribed`, the WAVs are still there, the folder is still in
        # staging. Every reader worked it out again and none of them could
        # render it honestly, so the pipeline writes it down instead. The
        # transcript itself is fine — `meeting.transcription["status"]` still
        # says so — it is the *audio* that was not trusted enough to delete.
        meeting.status = MeetingStatus.GATE_FAILED
        meeting.save()
    # After the promotion, so the meeting that just finished is already in the
    # folder being indexed — and unconditional, because a meeting that stayed in
    # staging still changes the footer. `write_index` never raises: the dashboard
    # may not cost a transcript, the same rule diarization runs under.
    index.write_index(config)
    return meeting


def promote_meeting(meeting: Meeting, config: Config) -> bool:
    """Move a finished meeting out of staging and into the meetings folder.

    Called only once the WAVs are gone, which is the whole point: the meetings
    folder may be synced, and audio must never be written into it — see
    :meth:`referat.config.Config.staging_dir`. A meeting that kept its audio stays
    in staging and is promoted by a later `referat rerun` that comes out clean.

    Never raises. A meeting that cannot be moved is still a finished meeting with
    a transcript in it; it stays where it is, `referat list` still shows it, and
    the next rerun tries again. Losing the folder to a half-handled error would be
    a far worse outcome than leaving it in the wrong place.
    """
    if meeting.dir.parent == config.paths.meetings_dir:
        return False
    if meeting.mic_path.exists() or meeting.system_path.exists():
        # Enforced here and not only at the call site: "no WAV ever reaches the
        # meetings folder" is the invariant this whole split exists for, and it
        # should not depend on every future caller remembering the order.
        log.debug("%s keeps its audio; staying in %s", meeting.id, meeting.dir.parent)
        return False
    try:
        moved = paths.move_meeting_dir(meeting.dir, config.paths.meetings_dir)
    except OSError:
        log.warning("could not move %s into the meetings folder", meeting.id, exc_info=True)
        return False
    meeting.dir = moved
    if moved.name != meeting.id:
        # new_meeting_dir reserves the id in both roots, so this should be
        # unreachable. If it ever fires, the folder name is the authority — that
        # is what `referat label <id>` and `find_meeting_dir` resolve against.
        log.warning("meeting %s was renamed to %s on the way in", meeting.id, moved.name)
        meeting.id = moved.name
        meeting.save()
    log.info("moved %s into %s", meeting.id, config.paths.meetings_dir)
    return True


def _transcription_meta(
    backend: Backend,
    transcripts: list[ChannelTranscript],
    elapsed: float,
    *,
    status: MeetingStatus = MeetingStatus.TRANSCRIBED,
    error: str | None = None,
) -> dict[str, Any]:
    """The `transcription` block of `meta.json`: what ran, and how well it went."""
    meta: dict[str, Any] = {
        "status": str(status),
        "model": backend.model,
        "device": backend.device,
        "compute_type": backend.compute_type,
        "seconds": round(elapsed, 2),
        "finished_at": dt.datetime.now().isoformat(timespec="seconds"),
        "channels": {t.channel: t.to_json() for t in transcripts},
        "audio_released": False,
    }
    if error:
        meta["error"] = error
    return meta
