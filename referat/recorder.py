r"""Dual-stream capture: the microphone and the system audio output, to WAV.

Two independent channels are written for every meeting:

* `mic.wav` — `sounddevice`, mono, 16 kHz. By definition one speaker, the user.
* `system.wav` — WASAPI loopback through `PyAudioWPatch`, captured at the output
  device's native mix rate and downmixed to mono. Everyone else, and it works
  through headphones, which is the whole point of using loopback.

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
import struct
import threading
import time
from pathlib import Path
from types import ModuleType

import numpy as np

from referat.config import Config
from referat.meeting import ChannelAudio, Meeting, MeetingStatus, Pause

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
        block_align = self.channels * SAMPLE_WIDTH
        return struct.pack(
            "<4sI4s4sIHHIIHH4sI",
            b"RIFF",
            36 + data_bytes,
            b"WAVE",
            b"fmt ",
            16,
            1,  # PCM
            self.channels,
            self.samplerate,
            self.samplerate * block_align,
            block_align,
            SAMPLE_WIDTH * 8,
            b"data",
            data_bytes,
        )

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


def resolve_mic_device(wanted: str) -> int | None:
    """Index of the input device matching `wanted`, or None for the system default.

    A substring that matches nothing is a warning, not an error: recording on
    the default microphone beats not recording at all. Several host APIs expose
    the same physical device, so WASAPI wins the tie when it is among them.
    """
    import sounddevice as sd

    if not wanted.strip():
        return None
    try:
        devices = sd.query_devices()
        hostapis = sd.query_hostapis()
    except Exception:
        log.exception("could not enumerate input devices")
        return None

    matches = [
        (i, d)
        for i, d in enumerate(devices)
        if d["max_input_channels"] > 0 and _matches(str(d["name"]), wanted)
    ]
    if not matches:
        log.warning("no input device matching %r; using the default", wanted)
        return None
    for index, device in matches:
        if "WASAPI" in str(hostapis[device["hostapi"]]["name"]).upper():
            log.info("mic device: %s (WASAPI)", device["name"])
            return index
    index, device = matches[0]
    log.info("mic device: %s", device["name"])
    return index


def resolve_loopback_device(audio: object, wanted: str) -> dict:
    """The WASAPI loopback device info matching `wanted`, else the default output's.

    `audio` is a live `pyaudiowpatch.PyAudio`. Raises :class:`RecorderError`
    when Windows exposes no loopback device at all.
    """
    try:
        candidates = list(audio.get_loopback_device_info_generator())  # type: ignore[attr-defined]
    except Exception as exc:
        raise RecorderError(f"no WASAPI loopback devices: {exc}") from exc

    if wanted.strip():
        for info in candidates:
            if _matches(str(info["name"]), wanted):
                log.info("loopback device: %s", info["name"])
                return info
        log.warning("no loopback device matching %r; using the default output", wanted)

    try:
        info = audio.get_default_wasapi_loopback()  # type: ignore[attr-defined]
    except Exception as exc:
        raise RecorderError(f"no default WASAPI loopback device: {exc}") from exc
    if info is None:
        raise RecorderError("no default WASAPI loopback device")
    log.info("loopback device: %s", info["name"])
    return info


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
        self._system: Channel | None = None
        self._mic_stream: object | None = None
        self._loopback_stream: object | None = None
        self._audio: object | None = None
        self._lock = threading.Lock()
        self._stopped = False

    # --- Start ---------------------------------------------------------------

    def start(self, started_at: dt.datetime | None = None) -> Meeting:
        """Create the meeting folder and begin writing both WAVs."""
        started_at = started_at or dt.datetime.now()
        meeting = Meeting.create(self.config.paths.meetings_dir, started_at)
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

        if self._mic is None and self._system is None:
            meeting.status = MeetingStatus.FAILED
            meeting.ended_at = started_at
            meeting.save()
            raise RecorderError("neither the microphone nor the system audio could be opened")

        meeting.status = MeetingStatus.RECORDING
        meeting.audio = self._audio_info()
        meeting.save()
        log.info(
            "recording %s (mic=%s, system=%s)",
            meeting.id,
            "on" if self._mic else "off",
            "on" if self._system else "off",
        )
        return meeting

    def _start_mic(self, meeting: Meeting) -> None:
        import sounddevice as sd

        device = resolve_mic_device(self.config.audio.mic_device)
        rate = self.config.audio.mic_samplerate
        try:
            stream = self._open_mic_stream(sd, device, rate)
        except Exception:
            # A device that will not do 16 kHz mono still gets recorded, at
            # whatever it does support; transcription resamples anyway.
            fallback = int(sd.query_devices(device, "input")["default_samplerate"])
            log.warning("mic rejected %d Hz; falling back to %d Hz", rate, fallback)
            stream = self._open_mic_stream(sd, device, fallback)
            rate = fallback

        name = str(sd.query_devices(device, "input")["name"])
        channel = Channel("mic", meeting.mic_path, rate, self.clock, device=name)
        self._mic = channel
        self._mic_stream = stream
        channel.start()
        stream.start()

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
        import pyaudiowpatch as pyaudio

        audio = pyaudio.PyAudio()
        self._audio = audio
        info = resolve_loopback_device(audio, self.config.audio.loopback_device)
        rate = int(info["defaultSampleRate"])
        channels = int(info["maxInputChannels"])

        channel = Channel(
            "system", meeting.system_path, rate, self.clock, device=str(info["name"])
        )
        self._system = channel

        def callback(in_data, frame_count, time_info, status):  # noqa: ANN001
            if in_data:
                channel.submit(_downmix(in_data, channels))
            return (None, pyaudio.paContinue)

        stream = audio.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=rate,
            input=True,
            input_device_index=int(info["index"]),
            frames_per_buffer=LOOPBACK_BLOCKSIZE,
            stream_callback=callback,
        )
        self._loopback_stream = stream
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
        self._close_stream(self._loopback_stream, "system")
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

        if meeting is None:
            return None

        meeting.ended_at = dt.datetime.now()
        meeting.duration_seconds = (meeting.ended_at - meeting.started_at).total_seconds()
        meeting.audio = self._audio_info()
        meeting.status = MeetingStatus.STOPPED
        meeting.save()
        for channel in self._channels():
            log.info(
                "%s: %.1fs captured (%.1fs of it padded silence)",
                channel.name,
                channel.writer.frames / channel.samplerate,
                channel.padded_frames / channel.samplerate,
            )
        log.info("stopped %s after %.1fs", meeting.id, meeting.duration_seconds)
        return meeting

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
        return [c for c in (self._mic, self._system) if c is not None]

    def _audio_info(self) -> dict[str, ChannelAudio]:
        from referat import paths

        info: dict[str, ChannelAudio] = {}
        if self._mic is not None:
            info["mic"] = self._mic.audio_info(paths.MIC_WAV)
        if self._system is not None:
            info["system"] = self._system.audio_info(paths.SYSTEM_WAV)
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
