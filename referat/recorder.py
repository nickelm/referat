r"""Dual-stream capture: the microphone and the system audio output, to WAV.

Two independent channels are written for every meeting:

* `mic.wav` — `sounddevice`, mono, 16 kHz. By definition one speaker, the user.
* `system.wav` — WASAPI loopback through `PyAudioWPatch`, captured at the output
  device's native mix rate and downmixed to mono. Everyone else, and it works
  through headphones, which is the whole point of using loopback.

**Every output is looped back, not just the default — since 2026-10-29.** A
loopback hears one render endpoint, and a call can play through any of them.
On 2026-10-28 a 28-minute Zoom call played through the Jabra SPEAK 510's own
speaker while the recorder listened to the laptop's, which was the Windows
default because no headphones were plugged in. The loopback delivered 4.5
seconds of packets in the whole meeting. The Jabra cancels its own playback out
of its microphone, so the mic held none of the far end either, and the meeting
was transcribed as the owner talking to nobody. The audio was released on
schedule, since a voiceless loopback is clean for every in-person meeting, and
the far end was never on disk to recover.

So with `[audio].loopback_device` empty, every endpoint gets its own stream and
its own :class:`Channel`, writing `system.part<N>.wav`. Each part is padded
against the one recording clock exactly as a single `system.wav` always was, so
the parts are aligned sample for sample, and :func:`mix_loopback_parts` combines
them into `system.wav` when recording stops. Nothing past the recorder ever sees
a part. A pinned `loopback_device` records the one endpoint straight into
`system.wav`, as before.

**Silence padding.** Windows stops handing out loopback packets when the render
endpoint is idle: measured on this machine, four seconds of silence produced
exactly zero frames. Written naively, `system.wav` would hold only the noisy
stretches back to back — every silence would vanish and everything after it
would shift earlier, which would quietly corrupt the two-channel merge in
:mod:`referat.merge`. So both channels are written against a
:class:`RecordingClock` and
padded with zeros whenever the file falls behind it. A position in either WAV
therefore always means the same moment of the meeting.

**Crash safety.** WAV size fields are rewritten in place every couple of
seconds and the file is fsynced, because :mod:`wave` only fixes its header on
`close()` — precisely what a kill skips. A process killed mid-meeting leaves two
playable files and a `meta.json` still marked `recording`.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import queue
import re
import struct
import threading
import time
import wave
from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np

from referat.config import Config
from referat.meeting import ChannelAudio, Meeting, MeetingStatus, Pause
from referat.resample import resample_chunks, to_mono_float

log = logging.getLogger(__name__)

SAMPLE_WIDTH = 2
"""Both channels are 16-bit PCM."""

FLUSH_INTERVAL_SECONDS = 2.0
"""How often the WAV header is rewritten and the file fsynced."""

PAD_TOLERANCE_SECONDS = 0.10
"""Only pad silence once a channel is at least this far behind the clock."""

WRITER_POLL_SECONDS = 0.5
"""Writer wake-up interval, so an idle loopback still grows in real time."""

MIC_BLOCKSIZE = 1600
"""0.1 s at 16 kHz."""

LOOPBACK_BLOCKSIZE = 1024

LOOPBACK_PART_RE = re.compile(r"^system\.part(\d+)\.wav$")
"""One endpoint's share of `system.wav` while a meeting records. See :func:`part_path`."""

MIX_BLOCK_SECONDS = 10.0
"""How much of each loopback part :func:`mix_loopback_parts` holds at once."""

ACTIVE_MIN_SECONDS = 0.5
"""Delivered audio, beyond padding, that makes a loopback part worth mixing in."""


class RecorderError(RuntimeError):
    """Raised when no channel at all could be opened."""


# --- Timing -----------------------------------------------------------------


class RecordingClock:
    """Elapsed time for one meeting, in two flavours.

    :meth:`audio_elapsed` counts only the time spent actually recording and is
    what a position in the WAV files means. :meth:`wall_elapsed` counts from the
    start regardless of pauses and is what `meta.json` records pause intervals
    in. Either can be derived from the other given the pause list.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._start = time.monotonic()
        self._paused_at: float | None = None
        self._paused_total = 0.0

    def wall_elapsed(self) -> float:
        return time.monotonic() - self._start

    def audio_elapsed(self) -> float:
        with self._lock:
            paused = self._paused_total
            if self._paused_at is not None:
                paused += time.monotonic() - self._paused_at
            return max(0.0, time.monotonic() - self._start - paused)

    def pause(self) -> float:
        """Stop the audio clock. Returns the wall-elapsed second it happened at."""
        with self._lock:
            if self._paused_at is None:
                self._paused_at = time.monotonic()
        return self.wall_elapsed()

    def resume(self) -> float:
        """Restart the audio clock. Returns the wall-elapsed second it happened at."""
        with self._lock:
            if self._paused_at is not None:
                self._paused_total += time.monotonic() - self._paused_at
                self._paused_at = None
        return self.wall_elapsed()

    @property
    def paused(self) -> bool:
        with self._lock:
            return self._paused_at is not None


# --- WAV --------------------------------------------------------------------

WAV_HEADER_FORMAT = "<4sI4s4sIHHIIHH4sI"
WAV_HEADER_BYTES = 44


def wav_header(samplerate: int, channels: int, data_bytes: int) -> bytes:
    """The canonical 44-byte PCM header, packed once here for the writer and the seal."""
    block_align = channels * SAMPLE_WIDTH
    return struct.pack(
        WAV_HEADER_FORMAT,
        b"RIFF",
        36 + data_bytes,
        b"WAVE",
        b"fmt ",
        16,
        1,  # PCM
        channels,
        samplerate,
        samplerate * block_align,
        block_align,
        SAMPLE_WIDTH * 8,
        b"data",
        data_bytes,
    )


def seal_wav(path: Path) -> int:
    """Make a WAV's header agree with its length, and return its frame count.

    The flush the killed process never made. :class:`WavWriter` rewrites the
    two size fields every :data:`FLUSH_INTERVAL_SECONDS`, so a file left by a
    process that died mid-recording is playable but its header stops up to two
    seconds short of the bytes actually on disk -- and `transcribe.decode_wav`
    reads through the stdlib `wave` module, which trusts the header and would
    leave that tail undecoded. This writes the header the writer would have
    written on close, from the file's own size and the format fields it already
    carries, and touches no sample.

    Only the two size fields are rewritten, and only when they disagree with
    the length, so sealing a file the writer closed properly writes nothing.
    """
    with path.open("r+b") as f:
        head = f.read(WAV_HEADER_BYTES)
        if len(head) < WAV_HEADER_BYTES or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
            raise ValueError(f"{path.name} is not a WAV this recorder wrote")
        fields = struct.unpack(WAV_HEADER_FORMAT, head)
        channels, samplerate, data_declared = fields[6], fields[7], fields[12]
        f.seek(0, os.SEEK_END)
        block_align = channels * SAMPLE_WIDTH
        data_bytes = (f.tell() - WAV_HEADER_BYTES) // block_align * block_align
        if data_bytes != data_declared:
            f.seek(0)
            f.write(wav_header(samplerate, channels, data_bytes))
            f.flush()
            os.fsync(f.fileno())
        return data_bytes // block_align


class WavWriter:
    """A PCM WAV file whose header is kept honest as it grows.

    The 44-byte canonical header is written up front with zero sizes; every
    flush seeks back to the two size fields, rewrites them, and fsyncs. That is
    the difference between a killed process leaving a playable file and leaving
    one every player rejects.
    """

    def __init__(self, path: Path, samplerate: int, channels: int = 1) -> None:
        self.path = path
        self.samplerate = samplerate
        self.channels = channels
        self._bytes = 0
        self._file = path.open("wb+")
        self._file.write(self._header(0))
        self._file.flush()

    def _header(self, data_bytes: int) -> bytes:
        return wav_header(self.samplerate, self.channels, data_bytes)

    @property
    def frames(self) -> int:
        return self._bytes // (SAMPLE_WIDTH * self.channels)

    def write(self, data: bytes) -> None:
        self._file.write(data)
        self._bytes += len(data)

    def sync(self) -> None:
        """Rewrite the size fields and force the bytes to disk."""
        self._file.seek(0)
        self._file.write(self._header(self._bytes))
        self._file.seek(0, os.SEEK_END)
        self._file.flush()
        os.fsync(self._file.fileno())

    def close(self) -> None:
        try:
            self.sync()
        finally:
            self._file.close()


# --- Loopback parts -----------------------------------------------------------


def part_path(folder: Path, number: int) -> Path:
    """Where loopback endpoint `number` (1-based, in the order opened) is recorded."""
    return folder / f"system.part{number}.wav"


def loopback_parts(folder: Path) -> list[Path]:
    """The loopback parts in a meeting folder, in the order their endpoints opened.

    Numerically rather than by name, so a machine with ten outputs does not put
    `part10` ahead of `part2` and mix at the wrong endpoint's rate.
    """
    found = []
    for path in folder.glob("system.part*.wav"):
        match = LOOPBACK_PART_RE.match(path.name)
        if match:
            found.append((int(match.group(1)), path))
    return [path for _, path in sorted(found)]


def _has_sound(path: Path) -> bool:
    """Whether a part holds any non-zero sample: the test when no counters survive."""
    with wave.open(str(path), "rb") as wav:
        step = int(MIX_BLOCK_SECONDS * wav.getframerate())
        while raw := wav.readframes(step):
            if np.frombuffer(raw, dtype=np.int16).any():
                return True
    return False


def _float_blocks(path: Path, rate: int) -> Iterator[np.ndarray]:
    """A part as mono float32 at `rate`, a block at a time, resampling if it must."""
    with wave.open(str(path), "rb") as wav:
        own, channels, frames = wav.getframerate(), wav.getnchannels(), wav.getnframes()
        if own != rate:
            yield from resample_chunks(wav, frames, channels, own, rate)
            return
        step = int(MIX_BLOCK_SECONDS * rate)
        while raw := wav.readframes(step):
            yield to_mono_float(raw, channels)


def mix_loopback_parts(
    target: Path, parts: list[Path], active: list[bool] | None = None
) -> list[int]:
    """Combine aligned loopback parts into `target`, delete them, and say which counted.

    `parts` is in the order the endpoints opened, so the first is the default
    output, and its rate is the mix rate. `active` says which parts carried
    sound. The recorder knows that from its counters. After a crash nothing
    does, so `None` means scan each part for a non-zero sample.

    Three cases, cheapest first. If no part carried sound, the first is renamed
    into place: it is silent, which is true of the meeting. If exactly one did,
    which is the ordinary call, that one is renamed and nothing is read or
    copied. If two or more did, they are summed a block at a time and clipped
    back to 16 bits. A part at a different rate is resampled with the same
    resampler transcription uses. Summing is exact for the int16 values
    involved, so a mix of one loud part and one silent one reproduces the loud
    one sample for sample.

    The mix is written beside `target` and renamed over it, and the parts are
    deleted only once `target` is in place: an exception leaves every part on
    disk. Returns the indices of the parts that went into `target`.
    """
    if not parts:
        raise ValueError("no loopback parts to mix")
    if active is None:
        active = [_has_sound(path) for path in parts]
    chosen = [i for i, on in enumerate(active) if on]

    if len(chosen) <= 1:
        used = chosen or [0]
        os.replace(parts[used[0]], target)
    else:
        used = chosen
        with wave.open(str(parts[0]), "rb") as wav:
            rate = wav.getframerate()
        mixing = target.with_name(target.name + ".mixing")
        block = int(MIX_BLOCK_SECONDS * rate)
        writer = WavWriter(mixing, rate)
        with ExitStack() as stack:
            try:
                sources: list[Iterator[np.ndarray] | None] = []
                for i in chosen:
                    source = _float_blocks(parts[i], rate)
                    stack.callback(source.close)
                    sources.append(source)
                buffers = [np.empty(0, dtype=np.float32) for _ in chosen]
                while True:
                    for n, source in enumerate(sources):
                        while source is not None and buffers[n].size < block:
                            piece = next(source, None)
                            if piece is None:
                                sources[n] = source = None
                            else:
                                buffers[n] = np.concatenate((buffers[n], piece))
                    take = max(min(b.size, block) for b in buffers)
                    if take == 0:
                        break
                    out = np.zeros(take, dtype=np.float32)
                    for n, buffered in enumerate(buffers):
                        count = min(buffered.size, take)
                        out[:count] += buffered[:count]
                        buffers[n] = buffered[count:]
                    out *= 32768.0
                    writer.write(np.clip(np.rint(out), -32768, 32767).astype(np.int16).tobytes())
            finally:
                writer.close()
        os.replace(mixing, target)

    for path in parts:
        if path.exists():
            try:
                path.unlink()
            except OSError:
                log.warning("could not delete loopback part %s", path.name, exc_info=True)
    return used


# --- One channel ------------------------------------------------------------


class Channel:
    """One WAV file plus the thread that feeds it.

    The audio callback only calls :meth:`submit`, which puts bytes on a queue —
    no disk I/O ever happens on a PortAudio thread. The writer thread drains the
    queue, drops everything while paused, and pads silence to keep the file
    aligned with the recording clock.
    """

    def __init__(
        self,
        name: str,
        path: Path,
        samplerate: int,
        clock: RecordingClock,
        *,
        device: str = "",
    ) -> None:
        self.name = name
        self.samplerate = samplerate
        self.device = device
        self.clock = clock
        self.writer = WavWriter(path, samplerate)
        self.padded_frames = 0
        self.dropped_blocks = 0
        self._queue: queue.Queue[bytes | None] = queue.Queue()
        self._paused = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"wav-{name}", daemon=True)

    # --- Fed by the audio callback ------------------------------------------

    def submit(self, data: bytes) -> None:
        """Hand one block of mono 16-bit PCM to the writer thread."""
        if self._paused.is_set():
            return
        self._queue.put(data)

    # --- Driven by the recorder ---------------------------------------------

    def start(self) -> None:
        self._thread.start()

    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    def close(self) -> None:
        self._queue.put(None)
        self._thread.join(timeout=5.0)
        if self._thread.is_alive():
            log.warning("%s writer did not finish in time", self.name)
        self.writer.close()

    def audio_info(self, filename: str) -> ChannelAudio:
        return ChannelAudio(
            file=filename,
            samplerate=self.samplerate,
            channels=1,
            frames=self.writer.frames,
            device=self.device,
        )

    # --- The writer thread ---------------------------------------------------

    def _run(self) -> None:
        last_sync = time.monotonic()
        while True:
            try:
                block = self._queue.get(timeout=WRITER_POLL_SECONDS)
            except queue.Empty:
                # Nothing arrived: an idle loopback delivers no frames at all,
                # so the file has to be grown here or the timeline collapses.
                self._pad_to_clock(0)
                block = b""
            if block is None:
                break
            if block and self._paused.is_set():
                # Queued just before the pause landed; the meeting says stop.
                self.dropped_blocks += 1
                continue
            if block:
                self._pad_to_clock(len(block))
                try:
                    self.writer.write(block)
                except OSError:
                    log.exception("%s: write failed", self.name)

            now = time.monotonic()
            if now - last_sync >= FLUSH_INTERVAL_SECONDS:
                try:
                    self.writer.sync()
                except OSError:
                    log.exception("%s: sync failed", self.name)
                last_sync = now

    def _pad_to_clock(self, incoming_bytes: int) -> None:
        """Write zeros for the stretch this channel missed before this block.

        `incoming_bytes` is the block about to be written; it belongs at the end
        of the timeline, so the gap goes in front of it.
        """
        if self._paused.is_set():
            return
        target = int(self.clock.audio_elapsed() * self.samplerate)
        have = self.writer.frames + incoming_bytes // SAMPLE_WIDTH
        gap = target - have
        if gap < PAD_TOLERANCE_SECONDS * self.samplerate:
            return  # Ahead, or close enough that jitter would just add noise.
        try:
            self.writer.write(bytes(gap * SAMPLE_WIDTH))
        except OSError:
            log.exception("%s: padding failed", self.name)
            return
        self.padded_frames += gap
        log.debug("%s: padded %.2fs of silence", self.name, gap / self.samplerate)


# --- Device selection -------------------------------------------------------


def _matches(name: str, wanted: str) -> bool:
    return wanted.lower() in name.lower()


def mic_device_candidates(wanted: str) -> list[int | None]:
    """Every way in to the wanted microphone, best first, ending in the default.

    **One device is not enough, which cost a meeting.** On 2026-09-02 the
    configured Jabra resolved to its WASAPI entry, that entry refused to open
    with `Insufficient memory [PaErrorCode -9992]` — PortAudio's report for a
    device another process is holding, among other things — and the recorder
    gave up, recording twenty-four minutes of an in-person meeting from the
    loopback alone. Windows was at that moment exposing the same physical
    microphone under MME, DirectSound and WDM-KS as well, and none of them was
    tried.

    So this returns a ranked list rather than a winner. WASAPI first, because it
    is the low-latency path and the one `[audio].mic_samplerate` is written for;
    then the other host APIs in enumeration order; then `None`, the system
    default, which is the answer to a microphone that has genuinely gone away
    with a change of room. A caller walks the list until a stream opens.

    The `None` at the end is a deliberate widening of what this used to do only
    when nothing matched: a laptop's own array is a poor substitute for the
    Jabra, and it is an enormously better one than silence.
    """
    import sounddevice as sd

    if not wanted.strip():
        return [None]
    try:
        devices = sd.query_devices()
        hostapis = sd.query_hostapis()
    except Exception:
        log.exception("could not enumerate input devices")
        return [None]

    matches = [
        (i, d)
        for i, d in enumerate(devices)
        if d["max_input_channels"] > 0 and _matches(str(d["name"]), wanted)
    ]
    if not matches:
        log.warning("no input device matching %r; using the default", wanted)
        return [None]

    def wasapi_first(pair: tuple[int, Any]) -> int:
        return 0 if "WASAPI" in str(hostapis[pair[1]["hostapi"]]["name"]).upper() else 1

    ranked = sorted(matches, key=wasapi_first)
    log.info(
        "mic candidates for %r: %s",
        wanted,
        ", ".join(f"{i} {d['name']}" for i, d in ranked),
    )
    return [i for i, _ in ranked] + [None]


def resolve_mic_device(wanted: str) -> int | None:
    """The device `[audio].mic_device` resolves to, or None for the system default.

    The first of :func:`mic_device_candidates`, which is what recording will
    actually try first. Kept as its own function because `referat devices` marks
    one row `<- config` and must not have its own opinion about which — a listing
    that disagreed with what gets recorded would be worse than no listing.
    """
    first = mic_device_candidates(wanted)[0]
    return first


def resolve_loopback_devices(audio: object, wanted: str) -> list[dict]:
    """Every WASAPI loopback endpoint to record, the default output's first.

    `audio` is a live `pyaudiowpatch.PyAudio`. With `wanted` empty that is every
    endpoint Windows exposes, because a call plays wherever its application
    sends it, and the default output is only where *Windows* sends things. See
    the module docstring for the meeting that cost. With `wanted` set it is the
    one endpoint matching it, or the default when nothing matches, as it always
    was. Raises :class:`RecorderError` when Windows exposes no loopback device
    at all.
    """
    try:
        candidates = list(audio.get_loopback_device_info_generator())  # type: ignore[attr-defined]
    except Exception as exc:
        raise RecorderError(f"no WASAPI loopback devices: {exc}") from exc

    if wanted.strip():
        for info in candidates:
            if _matches(str(info["name"]), wanted):
                log.info("loopback device: %s", info["name"])
                return [info]
        log.warning("no loopback device matching %r; using the default output", wanted)

    try:
        default = audio.get_default_wasapi_loopback()  # type: ignore[attr-defined]
    except Exception:
        log.warning("Windows named no default output", exc_info=True)
        default = None
    if wanted.strip() or not candidates:
        if default is None:
            raise RecorderError("no default WASAPI loopback device")
        log.info("loopback device: %s", default["name"])
        return [default]

    if default is not None:
        candidates.sort(key=lambda info: info["index"] != default["index"])
    log.info("loopback devices: %s", ", ".join(str(info["name"]) for info in candidates))
    return candidates


def resolve_loopback_device(audio: object, wanted: str) -> dict:
    """The first of :func:`resolve_loopback_devices`: the default, or the pinned one."""
    return resolve_loopback_devices(audio, wanted)[0]


# --- The recorder -----------------------------------------------------------


class Recorder:
    """Captures one meeting. Single use: build a new one per meeting.

    If only one of the two channels can be opened the meeting still records —
    half a meeting is worth far more than none. Only when both fail does
    :meth:`start` raise.
    """

    def __init__(self, config: Config) -> None:
        self.config = config
        self.meeting: Meeting | None = None
        self.clock = RecordingClock()
        self._mic: Channel | None = None
        self._systems: list[Channel] = []
        """One per loopback endpoint that opened, the default output's first."""
        self._mic_stream: object | None = None
        self._loopback_streams: list[object] = []
        self._system_parts = False
        """Whether the loopback is being written as parts, to be mixed on stop."""
        self._system_used: list[int] = []
        self._audio: object | None = None
        self.missing_channels: list[str] = []
        """Channels that would not open this run: read by the tray for its toast."""
        self._lock = threading.Lock()
        self._stopped = False

    # --- Start ---------------------------------------------------------------

    def start(self, started_at: dt.datetime | None = None) -> Meeting:
        """Create the meeting folder and begin writing both WAVs."""
        started_at = started_at or dt.datetime.now()
        meeting = Meeting.create(self.config, started_at)
        self.meeting = meeting
        self.clock = RecordingClock()

        try:
            self._start_mic(meeting)
        except Exception:
            log.exception("microphone capture unavailable")
        try:
            self._start_system(meeting)
        except Exception:
            log.exception("system audio capture unavailable")

        if self._mic is None and not self._systems:
            meeting.status = MeetingStatus.FAILED
            meeting.ended_at = started_at
            meeting.save()
            raise RecorderError("neither the microphone nor the system audio could be opened")

        meeting.status = MeetingStatus.RECORDING
        meeting.audio = self._audio_info()
        # Written down rather than left to be noticed. A channel that never
        # opened used to show up only as an *absent* key under `audio`, which is
        # the "infer it from what is missing" shape this codebase refuses
        # everywhere else — and no reader inferred it, so `referat list` called a
        # meeting with no microphone in it `transcribed` and nothing ever said
        # otherwise. `missing_channels` is the fact; the toast and the icon are
        # how somebody finds out in time to do something about it.
        self.missing_channels = [
            name
            for name, opened in (("mic", self._mic is not None), ("system", bool(self._systems)))
            if not opened
        ]
        meeting.missing_channels = list(self.missing_channels)
        meeting.save()
        log.info(
            "recording %s (mic=%s, system=%s)",
            meeting.id,
            "on" if self._mic else "off",
            f"{len(self._systems)} output(s)" if self._systems else "off",
        )
        if self._mic is None:
            # Loud, because for a meeting held in a room the microphone *is* the
            # recording and the loopback is the silent one. Twenty-four minutes
            # were lost to this being an INFO line.
            log.error(
                "%s is recording WITHOUT A MICROPHONE - only system audio will be "
                "captured, which is silence unless this is a call",
                meeting.id,
            )
        return meeting

    def _start_mic(self, meeting: Meeting) -> None:
        """Open the first microphone that will actually open, and record from it.

        Every candidate from :func:`mic_device_candidates` is tried in turn, and
        each is tried at the configured rate and then at its own default rate.
        One device used to be the whole of this, and the day it refused —
        `-9992`, with three other routes to the same Jabra sitting in the device
        list — an in-person meeting was recorded from the loopback alone.

        The rate retry is guarded on the two rates *differing*, which it was not.
        `default_samplerate` for that Jabra is 16000, which is exactly what
        `[audio].mic_samplerate` asks for, so the fallback re-attempted the rate
        that had just failed and logged "falling back to 16000 Hz" from 16000 Hz.
        It was also answering a failure that had nothing to do with rate: a
        device held by another process refuses every rate equally.
        """
        import sounddevice as sd

        wanted = self.config.audio.mic_samplerate
        failures: list[str] = []
        for device in mic_device_candidates(self.config.audio.mic_device):
            try:
                info = sd.query_devices(device, "input")
            except Exception as exc:
                failures.append(f"device {device}: {exc}")
                continue
            name = str(info["name"])
            default = int(info["default_samplerate"])
            # Deduplicated, so a device whose default *is* the configured rate is
            # tried once rather than twice with the same argument.
            for rate in dict.fromkeys((wanted, default)):
                try:
                    stream = self._open_mic_stream(sd, device, rate)
                except Exception as exc:
                    failures.append(f"{name} at {rate} Hz: {exc}")
                    log.warning("mic %s refused %d Hz: %s", name, rate, exc)
                    continue
                if rate != wanted:
                    # Recorded at whatever the device does support; transcription
                    # resamples anyway.
                    log.warning("mic %s took %d Hz rather than %d", name, rate, wanted)
                channel = Channel("mic", meeting.mic_path, rate, self.clock, device=name)
                self._mic = channel
                self._mic_stream = stream
                channel.start()
                stream.start()
                log.info("mic device: %s at %d Hz", name, rate)
                return

        raise RecorderError(
            "no microphone could be opened; tried " + "; ".join(failures)
        )

    def _open_mic_stream(self, sd: ModuleType, device: int | None, rate: int) -> object:
        def callback(indata, frames, time_info, status) -> None:  # noqa: ANN001
            if status:
                log.debug("mic stream status: %s", status)
            if self._mic is not None:
                self._mic.submit(bytes(indata))

        stream = sd.InputStream(  # type: ignore[attr-defined]
            samplerate=rate,
            channels=1,
            dtype="int16",
            blocksize=MIC_BLOCKSIZE,
            device=device,
            callback=callback,
        )
        return stream

    def _start_system(self, meeting: Meeting) -> None:
        """Open a loopback on every endpoint :func:`resolve_loopback_devices` names.

        One endpoint is written straight into `system.wav`. Several are written
        as parts and mixed on stop. An endpoint that will not open is logged and
        skipped. The channel is missing only when none opens, because any one
        of them may be the one the call is playing through.
        """
        import pyaudiowpatch as pyaudio

        audio = pyaudio.PyAudio()
        self._audio = audio
        infos = resolve_loopback_devices(audio, self.config.audio.loopback_device)
        self._system_parts = len(infos) > 1
        failures: list[str] = []
        for info in infos:
            name = str(info["name"])
            path = (
                part_path(meeting.dir, len(self._systems) + 1)
                if self._system_parts
                else meeting.system_path
            )
            try:
                self._open_loopback(pyaudio, audio, info, path)
            except Exception as exc:
                failures.append(f"{name}: {exc}")
                log.warning("loopback %s would not open: %s", name, exc)
        if not self._systems:
            raise RecorderError("no loopback could be opened; tried " + "; ".join(failures))

    def _open_loopback(self, pyaudio: ModuleType, audio: object, info: dict, path: Path) -> None:
        rate = int(info["defaultSampleRate"])
        channels = int(info["maxInputChannels"])
        channel = Channel("system", path, rate, self.clock, device=str(info["name"]))

        def callback(in_data, frame_count, time_info, status):  # noqa: ANN001
            if in_data:
                channel.submit(_downmix(in_data, channels))
            return (None, pyaudio.paContinue)

        try:
            stream = audio.open(  # type: ignore[attr-defined]
                format=pyaudio.paInt16,
                channels=channels,
                rate=rate,
                input=True,
                input_device_index=int(info["index"]),
                frames_per_buffer=LOOPBACK_BLOCKSIZE,
                stream_callback=callback,
            )
        except Exception:
            # The writer opened the file already; a part nobody will feed is
            # one more WAV for the mix to read, so it goes now.
            channel.writer.close()
            path.unlink(missing_ok=True)
            raise
        self._systems.append(channel)
        self._loopback_streams.append(stream)
        channel.start()
        stream.start_stream()

    # --- Pause and resume ----------------------------------------------------

    def pause(self) -> None:
        """Stop appending samples and record the pause in `meta.json`."""
        with self._lock:
            if self._stopped or self.meeting is None or self.clock.paused:
                return
            at = self.clock.pause()
            for channel in self._channels():
                channel.pause()
            self.meeting.pauses.append(Pause(start=at))
            self.meeting.save()
        log.info("paused at %.1fs", at)

    def resume(self) -> None:
        """Start appending samples again and close the open pause interval."""
        with self._lock:
            if self._stopped or self.meeting is None or not self.clock.paused:
                return
            at = self.clock.resume()
            for channel in self._channels():
                channel.resume()
            if self.meeting.pauses and self.meeting.pauses[-1].end is None:
                self.meeting.pauses[-1].end = at
            self.meeting.save()
        log.info("resumed at %.1fs", at)

    # --- Stop ----------------------------------------------------------------

    def stop(self) -> Meeting | None:
        """Close both streams and finalize `meta.json`. Safe to call twice."""
        with self._lock:
            if self._stopped:
                return self.meeting
            self._stopped = True
            meeting = self.meeting

        if self.clock.paused and meeting is not None:
            # Stopped straight out of a pause: close the interval honestly.
            at = self.clock.resume()
            if meeting.pauses and meeting.pauses[-1].end is None:
                meeting.pauses[-1].end = at

        self._close_stream(self._mic_stream, "mic")
        for stream in self._loopback_streams:
            self._close_stream(stream, "system")
        for channel in self._channels():
            try:
                channel.close()
            except OSError:
                log.exception("could not close the %s channel", channel.name)
        if self._audio is not None:
            try:
                self._audio.terminate()  # type: ignore[attr-defined]
            except Exception:
                log.exception("could not terminate PyAudio")

        for channel in self._channels():
            log.info(
                "%s: %.1fs captured (%.1fs of it padded silence)%s",
                channel.name,
                channel.writer.frames / channel.samplerate,
                channel.padded_frames / channel.samplerate,
                f" from {channel.device}" if self._system_parts and channel.name == "system" else "",
            )
        if meeting is None:
            return None
        if self._system_parts:
            self._mix_system(meeting)

        meeting.ended_at = dt.datetime.now()
        meeting.duration_seconds = (meeting.ended_at - meeting.started_at).total_seconds()
        meeting.audio = self._audio_info()
        meeting.status = MeetingStatus.RECORDED
        meeting.save()
        log.info("stopped %s after %.1fs", meeting.id, meeting.duration_seconds)
        return meeting

    def _mix_system(self, meeting: Meeting) -> None:
        """Turn the loopback parts into `system.wav`, by the counters the channels kept.

        A part is active when its endpoint delivered sound beyond the padding,
        which is what Windows does only while something is playing there. If the
        mix fails, the most active part becomes `system.wav` so the meeting still
        has a loopback, and the rest stay on disk in staging. Promotion refuses a
        folder with a WAV in it, so they cannot reach the meetings folder.
        """
        parts = [channel.writer.path for channel in self._systems]
        delivered = [
            (channel.writer.frames - channel.padded_frames) / channel.samplerate
            for channel in self._systems
        ]
        active = [seconds > ACTIVE_MIN_SECONDS for seconds in delivered]
        started = time.monotonic()
        try:
            self._system_used = mix_loopback_parts(meeting.system_path, parts, active)
        except Exception:
            log.exception("%s: could not mix the loopback parts", meeting.id)
            best = max(range(len(parts)), key=lambda i: delivered[i])
            try:
                os.replace(parts[best], meeting.system_path)
                self._system_used = [best]
                log.error(
                    "%s: kept %s alone as system.wav; the other parts are still in %s",
                    meeting.id,
                    self._systems[best].device,
                    meeting.dir,
                )
            except OSError:
                log.exception("%s: no system.wav; the parts are still in %s", meeting.id, meeting.dir)
            return
        log.info(
            "%s: system.wav is %s (%.1fs)",
            meeting.id,
            " + ".join(self._systems[i].device for i in self._system_used),
            time.monotonic() - started,
        )

    @staticmethod
    def _close_stream(stream: object | None, name: str) -> None:
        if stream is None:
            return
        try:
            if hasattr(stream, "stop_stream"):  # PyAudio
                stream.stop_stream()  # type: ignore[attr-defined]
            else:  # sounddevice
                stream.stop()  # type: ignore[attr-defined]
            stream.close()  # type: ignore[attr-defined]
        except Exception:
            log.exception("could not close the %s stream", name)

    # --- Helpers -------------------------------------------------------------

    def _channels(self) -> list[Channel]:
        return ([self._mic] if self._mic is not None else []) + self._systems

    def _audio_info(self) -> dict[str, ChannelAudio]:
        """The `audio` block. With parts, `system` describes the mix.

        Before the stop, that is every endpoint opened, and the frames are the
        default output's. After it, it is the endpoints that went into
        `system.wav` and that file's real length.
        """
        from referat import paths

        info: dict[str, ChannelAudio] = {}
        if self._mic is not None:
            info["mic"] = self._mic.audio_info(paths.MIC_WAV)
        if self._systems:
            system = self._systems[0].audio_info(paths.SYSTEM_WAV)
            if self._system_parts:
                used = [self._systems[i] for i in self._system_used] or self._systems
                system.device = " + ".join(channel.device for channel in used)
                if self._system_used and self.meeting is not None:
                    try:
                        # From the file, because a part renamed into place
                        # keeps its own endpoint's rate.
                        with wave.open(str(self.meeting.system_path), "rb") as wav:
                            system.samplerate = wav.getframerate()
                            system.frames = wav.getnframes()
                    except (OSError, wave.Error):
                        log.warning("could not read back system.wav", exc_info=True)
            info["system"] = system
        return info


def _downmix(data: bytes, channels: int) -> bytes:
    """Interleaved 16-bit PCM to mono, averaging in int32 so it cannot overflow."""
    if channels == 1:
        return data
    samples = np.frombuffer(data, dtype=np.int16)
    usable = (samples.size // channels) * channels
    if usable == 0:
        return b""
    frames = samples[:usable].reshape(-1, channels).astype(np.int32)
    return frames.mean(axis=1).astype(np.int16).tobytes()
