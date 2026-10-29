"""Polyphase resampling of Referat's own 16-bit WAVs, in numpy and the stdlib alone.

Moved out of :mod:`referat.transcribe` on 2026-10-29 so that :mod:`referat.recorder`
can use it too. The recorder is base install and must not import `transcribe`, which
brings the diarizer, the GPU module and the rest of the pipeline in behind it. Its
use here is mixing several loopback endpoints into one `system.wav` when they run at
different mix rates. The code is unchanged by the move, so a `rerun` of an old
meeting decodes to the same samples it always did.

Why numpy and not scipy is in :func:`resample`'s docstring.
"""

from __future__ import annotations

import wave
from collections.abc import Iterator
from functools import lru_cache
from math import gcd

import numpy as np

DECODE_CHUNK_SECONDS = 60.0
"""How much of a resampled WAV is held as float32 at once. See :func:`referat.transcribe.decode_wav`."""

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
:func:`resample` agrees to 138 dB — which is float rounding — and puts an 11 kHz
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


def to_mono_float(raw: bytes, channels: int) -> np.ndarray:
    """Interleaved 16-bit PCM to mono float32 in -1.0 .. 1.0."""
    samples = np.frombuffer(raw, dtype=np.int16)
    if channels > 1:  # Not written by this recorder, but cheap to survive.
        usable = (samples.size // channels) * channels
        samples = samples[:usable].reshape(-1, channels).mean(axis=1)
    audio = samples.astype(np.float32)
    audio /= 32768.0  # In place: a `/` here would allocate the array twice.
    return audio


@lru_cache(maxsize=4)
def _kernel(up: int, down: int) -> tuple[np.ndarray, int]:
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
    :func:`resample_chunks` asks for it once per 60-second chunk.
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


def resample(block: np.ndarray, up: int, down: int) -> np.ndarray:
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
    It is the same judgement as :func:`referat.transcribe._neutralize_pyav` and
    :func:`referat.diarize._neutralize_torchcodec`, one layer down, and it is the
    project rule about keeping the import-critical path narrow rather than a new
    one.

    Contract-compatible with `resample_poly` on purpose, because meetings
    recorded before this existed have to re-run to the same samples: same output
    length, `ceil(n * up / down)`; same zero-phase alignment, output `m` at input
    time `m * down / up`; same zero padding past the ends; same default kernel.
    Verified against it at 138 dB on a real `system.wav` while it was importable.
    """
    kernel, half = _kernel(up, down)
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


def resample_chunks(
    wav: wave.Wave_read, frames: int, channels: int, rate: int, target_rate: int
) -> Iterator[np.ndarray]:
    """Resample a WAV to `target_rate` a chunk at a time, overlap-save.

    :func:`resample` low-pass filters as it decimates; plain
    decimation would alias speech down into the band Whisper listens to. Applied
    per chunk it would also ring at every seam, so each chunk is filtered with
    :data:`RESAMPLE_MARGIN` steps of real audio on either side and the margins
    are then trimmed off the output. Only the file's own two ends keep the
    resampler's zero padding, which is where it belongs — the result matches
    resampling the whole array at once to within float rounding.

    Chunk and margin are whole multiples of `down`, so every trim is an exact
    number of output samples and the pieces abut with no drift.

    A generator so a caller can hold one chunk rather than the file:
    :func:`resample_stream` collects them, and the recorder's loopback mixer sums
    them against other endpoints a chunk at a time.
    """
    divisor = gcd(rate, target_rate)
    up, down = target_rate // divisor, rate // divisor
    margin = RESAMPLE_MARGIN * down
    chunk = max(down, int(DECODE_CHUNK_SECONDS * rate) // down * down)

    position = 0
    while position < frames:
        take = min(chunk, frames - position)
        left = min(margin, position)
        right = (min(margin, frames - position - take) // down) * down
        wav.setpos(position - left)
        block = to_mono_float(wav.readframes(left + take + right), channels)
        if block.size == 0:  # A header claiming more frames than the file holds.
            return

        resampled = resample(block, up, down)
        head, tail = left // down * up, right // down * up
        yield resampled[head : resampled.size - tail] if tail else resampled[head:]
        position += take


def resample_stream(
    wav: wave.Wave_read, frames: int, channels: int, rate: int, target_rate: int
) -> np.ndarray:
    """The whole of :func:`resample_chunks`, in one preallocated array."""
    up, down = target_rate // gcd(rate, target_rate), rate // gcd(rate, target_rate)
    out = np.empty(-(-frames * up // down) + up, dtype=np.float32)
    written = 0
    for piece in resample_chunks(wav, frames, channels, rate, target_rate):
        out[written : written + piece.size] = piece
        written += piece.size
    return out[:written]
