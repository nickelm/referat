r"""Turning a meeting's WAV files into `transcript.md`, with `faster-whisper`.

Runs on the GPU when there is one and degrades to the CPU when there is not,
because a transcript produced slowly beats no transcript at all. Everything here
is called from a background thread owned by :mod:`referat.tray`, and at build
step 8 from `referat rerun`, so nothing in this module touches the state machine
or the UI: it takes a :class:`referat.meeting.Meeting`, works on the files in its
folder, and writes the results back into `meta.json`.

**Both channels are transcribed.** The microphone is one speaker by definition
and gets `ME`; the loopback channel is everyone else, and :mod:`referat.diarize`
splits it into `SPEAKER_01`, `SPEAKER_02`, ... — falling back to an
undifferentiated `REMOTE` whenever it cannot. Ordering the two into one document
belongs to :mod:`referat.merge`.

**Imports are lazy.** `faster_whisper` and `torch` live behind the `transcribe`
extra, ~3 GB of wheels a base install does not have, so they are imported inside
the functions that need them, exactly as :mod:`referat.recorder` does with
`sounddevice` and `pyaudiowpatch`. Importing this module must stay free.

**Audio is decoded here, not by faster-whisper.** See :func:`decode_wav` and
:func:`_neutralize_pyav`.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import sys
import threading
import time
import wave
from dataclasses import dataclass, field
from math import gcd
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np

from referat import diarize, merge, paths
from referat.config import Config
from referat.meeting import Meeting, MeetingStatus

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
"""What Whisper wants, and what `mic.wav` is already recorded at."""

SAMPLE_WIDTH = 2
"""Both channels are 16-bit PCM, as written by :mod:`referat.recorder`."""

ME_LABEL = "ME"
"""The microphone channel is one speaker by definition: the owner of the laptop."""

REMOTE_LABEL = "REMOTE"
"""Everyone on the loopback channel, when diarization did not name them."""

DECODE_CHUNK_SECONDS = 60.0
"""How much of a resampled WAV is held as float32 at once. See :func:`decode_wav`."""

RESAMPLE_MARGIN = 1024
"""Overlap-save margin for chunked resampling, in `down`-sized input steps.

`resample_poly`'s filter is `2 * 10 * max(up, down) + 1` taps wide — 61 for the
48 kHz -> 16 kHz case — so a thousand-odd steps of history on each side is
enormous overkill, which is the point: the seams have to be inaudible and this
costs 64 ms of redundant filtering per chunk.
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
    speakers: list[str] = field(default_factory=list)
    """The `SPEAKER_NN` labels diarization found here, in order of first
    appearance. Empty on the mic channel and on any undiarized run."""
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
            meta["speakers"] = self.speakers
            meta["diarization"] = self.diarization.to_json()
        return meta


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
    with wave.open(str(path), "rb") as wav:
        rate, channels, width = wav.getframerate(), wav.getnchannels(), wav.getsampwidth()
        frames = wav.getnframes()
        if width != SAMPLE_WIDTH:
            raise TranscriptionError(f"{path.name} is {width * 8}-bit, expected 16-bit PCM")
        if rate == target_rate:
            return _to_mono_float(wav.readframes(frames), channels)
        audio = _resample_stream(wav, frames, channels, rate, target_rate)
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


def _resample_stream(
    wav: wave.Wave_read, frames: int, channels: int, rate: int, target_rate: int
) -> np.ndarray:
    """Resample a WAV to `target_rate` a chunk at a time, overlap-save.

    `scipy.signal.resample_poly` low-pass filters as it decimates; plain
    decimation would alias speech down into the band Whisper listens to. Applied
    per chunk it would also ring at every seam, so each chunk is filtered with
    :data:`RESAMPLE_MARGIN` steps of real audio on either side and the margins
    are then trimmed off the output. Only the file's own two ends keep the
    resampler's zero padding, which is where it belongs — the result matches
    resampling the whole array at once to within float rounding.

    Chunk and margin are whole multiples of `down`, so every trim is an exact
    number of output samples and the pieces abut with no drift.
    """
    from scipy.signal import resample_poly

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

        resampled = resample_poly(block, up, down).astype(np.float32, copy=False)
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
    path: Path,
    model: Any,
    config: Config,
    label: str,
    *,
    diarize_channel: bool = False,
    device: str = "cpu",
) -> ChannelTranscript:
    """Run one WAV file through the model and collect its segments.

    `diarize_channel` additionally asks :mod:`referat.diarize` who spoke, which
    only the loopback channel wants: the microphone is one speaker by
    definition. `device` is the transcription backend's, so diarization follows
    Whisper onto the GPU or the CPU rather than choosing for itself.
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
        _diarize_into(transcript, audio, config, device)
    return transcript


def _diarize_into(
    transcript: ChannelTranscript, audio: np.ndarray, config: Config, device: str
) -> None:
    """Name the speakers of one channel, in place. Never raises.

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
        transcript.segments, transcript.speakers = diarize.assign(
            transcript.segments, result.turns, transcript.label
        )
        log.info(
            "%s: %s", transcript.channel, ", ".join(transcript.speakers) or "no speakers found"
        )
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
    # Only the loopback channel is diarized: the microphone is the owner of the
    # laptop and nobody else, so there is nothing in it to tell apart.
    channels = (
        (meeting.mic_path, ME_LABEL, False),
        (meeting.system_path, REMOTE_LABEL, True),
    )
    with _RUN_LOCK:
        model = load_model(backend)
        try:
            results: list[ChannelTranscript] = []
            errors: dict[str, str] = {}
            for path, label, diarize_channel in channels:
                if not path.exists():
                    log.warning("no %s in %s", path.name, meeting.dir)
                    continue
                try:
                    results.append(
                        transcribe_channel(
                            path,
                            model,
                            config,
                            label,
                            diarize_channel=diarize_channel,
                            device=backend.device,
                        )
                    )
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


def render_transcript(meeting: Meeting, entries: list[tuple[float, str, str]]) -> str:
    """The `transcript.md` document, from `(start, label, text)` in time order.

    Takes already-ordered entries rather than channels; ordering the channels is
    :func:`referat.merge.merge`'s job.
    """
    when = meeting.started_at.strftime("%Y-%m-%d %H:%M")
    minutes = round(meeting.duration_seconds / 60)
    lines = [f"## Meeting {when} ({minutes} min)", ""]
    lines += [f"[{format_timestamp(start)}] {label}: {text}" for start, label, text in entries]
    return "\n".join(lines) + "\n"


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

    The `audio` block itself stays — frames, duration and device remain on record
    — and `audio_released` says the files are gone deliberately.
    """
    if meeting.status is not MeetingStatus.DONE:
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

    freed = 0
    for name in present:
        wav = meeting.dir / str(meeting.audio[name].file)
        try:
            freed += wav.stat().st_size
            wav.unlink()
        except OSError:
            log.exception("could not delete %s", wav)
            return False
    meeting.transcription["audio_released"] = True
    meeting.save()
    log.info("released %.0f MB of audio from %s", freed / 1e6, meeting.id)
    return True


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
    status = MeetingStatus.FAILED if errors else MeetingStatus.DONE
    meeting.status = status
    meeting.transcription = _transcription_meta(backend, transcripts, elapsed, status=status)
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

    release_audio_if_clean(meeting)
    return meeting


def _transcription_meta(
    backend: Backend,
    transcripts: list[ChannelTranscript],
    elapsed: float,
    *,
    status: MeetingStatus = MeetingStatus.DONE,
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
