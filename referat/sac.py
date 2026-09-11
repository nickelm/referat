r"""Smart App Control: recognising a block, probing for one, and outliving one.

**Smart App Control is enforcing on this machine**, and what it refuses is a
native file whose publisher it cannot vouch for. Every one of scipy's 106
`.pyd` files is unsigned, `pyannote.audio` reaches scipy through
`lightning -> torchmetrics -> scipy.signal` with no seam to cut, and the
refusal is **a window rather than a state**: a file is blocked until the cloud
reputation for it arrives, and the window re-opens for a file it once allowed.
On 2026-09-01 it walked `_odepack`, `_stats_pythran` and `_sobol` in turn over
two hours; on 2026-09-10 it opened on `_qhull` at 09:51, was still open on
`_slsqplib` at 10:40, and the pattern lines up with three Windows updates
installed on the 8th and 9th. Nothing in this process can close it, and there
is no per-file allow.

So this module does the three things that *are* possible, and
:func:`referat.transcribe.audio_is_clean` does the fourth — keeping the audio,
because a channel diarization failed on is a channel whose speaker names can
only ever come back from the WAV, and the 2026-09-10 meeting lost them by
releasing it thirteen seconds after the failure.

1. :func:`is_block` says whether an exception *is* a Smart App Control refusal,
   walking the cause chain, so a retry can tell it from every other
   `ImportError` — which must not be retried, because a gated repository does
   not come right by waiting.
2. :func:`probe` walks the import chain transcription and diarization walk, in
   a **child process**, and reports what was refused. A child because the whole
   point is to load the DLLs and an unsigned load in the tray's own process is
   the one thing the recorder must never depend on; the tray runs it when it
   starts and when a meeting starts, so a block is known before the meeting
   ends rather than after, and again while waiting to re-transcribe one.
3. The retry that :func:`referat.diarize.diarize` runs is short, because the
   window is measured in hours and a job holding the GPU for hours is worse
   than the names being late. The long wait is the tray's
   (`App._recover_blocked`), which re-probes on a timer and re-runs the
   meeting once the probe comes back clean, from the audio the gate kept.

Pure stdlib, importable in the base install: the CLI's `referat probe` and
the tray both need it, and neither carries torch. The probe imports the heavy
libraries in the child and nowhere else.
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)

BLOCK_TEXT = "An Application Control policy has blocked this file"
"""The message Windows puts in the `OSError` the loader raises, verbatim."""

_MODULE_RE = re.compile(r"importing (\w+)")

OK = "ok"
BLOCKED = "blocked"
ERROR = "error"
"""A :class:`Probe`'s `status`: the chain loaded, Smart App Control refused a
file in it, or the probe could not run at all — the `transcribe` extra not
installed, a timeout, a crash — which says nothing either way."""

PROBE_TIMEOUT = 300.0
"""Seconds. A cold import of torch and pyannote takes ten to twenty; the margin
is for a machine paging."""

_PROBE_SCRIPT = """
import sys
from referat import diarize, transcribe
transcribe._neutralize_pyav()
diarize._neutralize_torchcodec()
import torch
import faster_whisper
import pyannote.audio
print("referat-probe: ok")
"""
"""What the child runs: the two stubs the pipeline applies, then the two
imports it makes, in the pipeline's own order. Nothing is instantiated and no
model is loaded — a block happens at import, which is the whole of what this
asks about."""


def is_block(exc: BaseException) -> bool:
    """Whether `exc`, or anything in its cause chain, is Smart App Control refusing a file."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if BLOCK_TEXT in str(current):
            return True
        current = current.__cause__ or current.__context__
    return False


def blocked_module(exc: BaseException) -> str:
    """The module name in a block's message — `_qhull`, `_slsqplib` — or `""`.

    Walks the same chain :func:`is_block` does, so a block wrapped in a
    `RuntimeError` by a library on the way up still names its file.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        text = str(current)
        if BLOCK_TEXT in text:
            match = _MODULE_RE.search(text)
            return match.group(1) if match else "a native module"
        current = current.__cause__ or current.__context__
    return ""


@dataclass(frozen=True)
class Probe:
    """What one :func:`probe` found."""

    status: str
    blocked: str = ""
    """The module refused, when `status` is :data:`BLOCKED`."""
    detail: str = ""
    """The line that decided it, for the log."""
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status == OK

    def describe(self) -> str:
        """One sentence for a log line, a notification or `referat probe`."""
        if self.status == OK:
            return f"Smart App Control is not blocking the transcription stack ({self.seconds:.0f}s)"
        if self.status == BLOCKED:
            return f"Smart App Control is blocking {self.blocked} ({self.detail})"
        return f"could not probe Smart App Control: {self.detail}"


def probe(python: str | None = None, timeout: float = PROBE_TIMEOUT) -> Probe:
    """Load the transcription stack in a child process and report what was refused.

    `python` defaults to this interpreter, which in the tray is the venv's
    `pythonw.exe` — fine for `-c` with captured pipes. The child gets no
    console window, for the reason :func:`referat.notes._spawn` gives.

    Never raises. A probe that cannot run answers :data:`ERROR`, which a caller
    must read as *unknown* and never as *clear*: the tray waiting to re-run a
    meeting must not take a crashed probe as permission.
    """
    args = [python or sys.executable, "-c", _PROBE_SCRIPT]
    started = time.monotonic()
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return Probe(ERROR, detail=f"timed out after {timeout:.0f}s", seconds=time.monotonic() - started)
    except OSError as exc:
        return Probe(ERROR, detail=str(exc), seconds=time.monotonic() - started)
    elapsed = time.monotonic() - started

    if completed.returncode == 0 and "referat-probe: ok" in completed.stdout:
        return Probe(OK, seconds=elapsed)
    lines = [line.strip() for line in completed.stderr.splitlines() if line.strip()]
    for line in reversed(lines):
        if BLOCK_TEXT in line:
            match = _MODULE_RE.search(line)
            return Probe(
                BLOCKED,
                blocked=match.group(1) if match else "a native module",
                detail=line,
                seconds=elapsed,
            )
    last = lines[-1] if lines else f"exit code {completed.returncode} and no output"
    return Probe(ERROR, detail=last, seconds=elapsed)
