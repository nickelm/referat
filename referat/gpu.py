r"""Giving the GPU back between jobs.

`transcribe_channels` and `diarize._run` both end by dropping their model, and
both said in their docstrings that this was so the VRAM would not stay occupied
for the rest of the session. It did anyway. Measured on 2026-09-02: a tray idle
since its last meeting held **8484 MiB of 12227**, and a `referat rerun` in a
second process stalled inside `ctranslate2.models.Whisper(...)` with the 3.7 GB
that were left.

`del` returns the *Python reference*, not the memory. Torch's CUDA caching
allocator keeps every block it has ever taken from the driver, so that the next
allocation of the same size is free; nothing but
:func:`torch.cuda.empty_cache` hands the arena back, and nothing in this package
was calling it. That is this module: one function, called from each of the three
places that finish with a model on the GPU.

**What it can and cannot reclaim.** CTranslate2 — which is what faster-whisper
actually runs on — allocates outside torch entirely, so `empty_cache` never sees
the Whisper weights. What this reclaims is torch's share, which is pyannote. And
the CUDA *context* itself, a few hundred MiB, lives as long as the process does:
the target is leaving room for a second large-v3, not reaching zero.

**This file used to add "those come back when the `WhisperModel` is destroyed,
which `del model` already does".** That sentence was doing work it could not
support. `del` destroys the model only when nothing else references it, and on
2026-09-03 a tray idle between jobs was measured holding 5205 MiB of 12227 while
this function reported *released 0 MiB* — torch had nothing cached, so the
residue was CTranslate2's and this function was never going to be the one to
return it. The next job reached 11424 MiB with 468 MiB spilled to host memory,
and diarization took 6527.8s against 58.5s for the previous meeting.

**Whether the skipped `gc.collect()` is what held it is unproven**, and worth
saying because the guess is so tempting. In a standalone process the retention
does not reproduce at all: `del` alone returns the full 3788 MiB with cyclic GC
disabled, and the floor is flat across three jobs, pyannote on a worker thread
included. So something about the tray — Qt in the process, that skip, or neither
— is the difference, and it is still open in `TODO.md`.

The fix therefore does not rely on knowing.
:func:`referat.transcribe.unload_model` frees the weights through CTranslate2's
own API just before the `del`, on whatever thread asks. **Between them the two
functions cover both allocators, and neither covers the other's.**
"""

from __future__ import annotations

import gc
import logging
import threading

log = logging.getLogger(__name__)

MIB = 1024 * 1024


def release(where: str) -> None:
    """Hand torch's CUDA arena back to the driver, and say how much came back.

    Never raises, for the same reason :func:`referat.diarize.diarize` never
    does: this runs in the `finally` of a block that may already be unwinding an
    exception, and a failure to reclaim memory may not become the failure the
    caller sees.

    Guarded on ``torch.cuda.is_initialized()`` rather than ``is_available()``,
    which would *create* a CUDA context on a machine that had deliberately
    stayed on the CPU — the reverse of the point. A CPU run therefore costs an
    attribute lookup and nothing else.

    ``gc.collect()`` first, because torch frees only what has no live
    references: the `del` this is called after leaves the tensors reachable from
    a traceback, a cycle, or the frame of the function still unwinding.

    **But only on the main thread, and that restriction is not about torch.**
    Since step 20 this process also holds Qt, and a `gc.collect()` destroys
    whatever it reaps *on the thread that called it* — including the C++ half of
    any unreachable PySide6 widget. Destroying a QWidget off the GUI thread is
    undefined behaviour, and the tray calls this from its `transcribe` daemon
    thread, right after a job that has been rebuilding the meetings tree for
    minutes. That is the second wrong-thread hazard the 2026-09-03 heap
    corruption turned up; :meth:`referat.ui.shell.Shell.notify` was the first and
    the one that was actually firing.

    Skipping it costs the cycles and the unwinding frames: `empty_cache` still
    hands back everything the `del` above already made unreachable, which is the
    overwhelming majority and the reason this module exists. A CLI `referat
    rerun` runs on the main thread with no Qt in the process and still collects.

    **This used to end "and nothing else", and that clause is withdrawn.** The
    2026-09-03 residue is unexplained, this skip is the obvious candidate, and
    nobody has shown it either way — so the honest form is that skipping the
    collection costs whatever is only reachable through a cycle, and that the
    size of that has been measured once at zero in a standalone process and never
    in the tray.
    """
    try:
        import torch

        if not torch.cuda.is_initialized():
            return
        reserved = torch.cuda.memory_reserved()
        if threading.current_thread() is threading.main_thread():
            gc.collect()
        torch.cuda.empty_cache()
        freed = reserved - torch.cuda.memory_reserved()
        # What the driver can now hand somebody else, which is the number
        # `nvidia-smi` reports and the only one that answers the question this
        # module exists for: is there room for a second large-v3? It counts
        # CTranslate2's arena and the CUDA context too, neither of which torch
        # knows about, so it is also how much of the residue is *not* torch's.
        free, total = torch.cuda.mem_get_info()
    except Exception:
        log.debug("could not release CUDA memory after %s", where, exc_info=True)
        return
    log.info(
        "released %.0f MiB after %s; %.0f MiB of %.0f MiB free on the device",
        freed / MIB,
        where,
        free / MIB,
        total / MIB,
    )
