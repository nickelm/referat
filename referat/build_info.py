"""When the code that is running was last written, and when it started running.

**There is no build to date.** Referat is installed editable — `pip install -e .`
puts the checkout on `sys.path` — so the `.py` files in `referat/` *are* the
running program, and there is no wheel, no artifact and no build step whose date
could be stamped anywhere. The honest stand-in is the newest modification time
across the package's own modules, which is the thing that changes when the code
changes.

**`CODE_MTIME` is sampled once, at import, and that is the point rather than an
optimization.** A tray runs for days and the files under it get edited while it
runs. A timestamp read when the menu is drawn would describe the code on disk —
which, in the one case worth asking about, is exactly the code the tray is *not*
running. Read at import, it describes what was loaded, which is the question
"am I running the most recent build?" actually asks.

Comparing that constant against a fresh :func:`source_mtime` is what detects a
stale process, and `referat status` does it every time it is run.

**The tray used not to be able to do this for itself, and since step 20 it
could.** The obstacle was `pystray`'s: it builds the Win32 menu in
`update_menu()` and reuses the handle on right-click, so callable menu text was
evaluated when a state *transition* happened rather than when the menu was
opened — and a tray sitting idle is precisely the one that would have kept
reporting itself fresh. A warning silently absent when it matters is worse than
no warning, so the menu carried the two timestamps and left the comparison
alone. Qt has no such problem: a `QMenu` emits `aboutToShow`, and its actions are
live objects whose text can be set at that moment. Nothing has been built on
that yet, and this paragraph says so rather than keeping an argument whose
premise has gone.

Git is deliberately not consulted. A commit date is wrong in exactly the case
this exists for: code edited and not yet committed still reports the old commit
while the tray runs the new file.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from referat import __version__

_PACKAGE = Path(__file__).resolve().parent

STAMP = "%Y-%m-%d %H:%M"
"""Minutes, not seconds. This is read by eye against "when did I last edit?"."""


def source_mtime(package: Path | None = None) -> dt.datetime | None:
    """The newest modification time across the package's modules, read now.

    `None` when there is nothing to stat — an unreadable directory, or a form of
    this package that is not loose files. Never raises: a version line may not
    cost a tray.
    """
    folder = package or _PACKAGE
    newest = 0.0
    try:
        # `rglob`, not `glob`. This read `*.py` and so saw nothing inside
        # `referat/ui/`, which since step 20 is the command center and the tray's
        # own shell — a third of the package and the part that changes most.
        # A tray running an hour-old window reported itself current, and during
        # the 2026-09-03 crash hunt that stamp was the evidence that nearly
        # exonerated the code that was actually crashing.
        modules = list(folder.rglob("*.py"))
    except OSError:
        return None
    for module in modules:
        try:
            newest = max(newest, module.stat().st_mtime)
        except OSError:
            continue
    if not newest:
        return None
    # Whole seconds, because `status.json` stores this with
    # `isoformat(timespec="seconds")` and :func:`outdated` compares a value that
    # has been through that file against one read straight off the disk. With
    # microseconds kept, the two differ by a fraction the serialization dropped
    # and every freshly started tray reports itself out of date.
    return dt.datetime.fromtimestamp(newest).replace(microsecond=0)


CODE_MTIME: dt.datetime | None = source_mtime()
"""The code this process loaded. Sampled at import; see the module docstring."""

STARTED_AT: dt.datetime = dt.datetime.now()
"""When this process started, near enough — this module is imported at startup."""


def _clock(moment: dt.datetime, *, today: dt.date | None = None) -> str:
    """`09:12` for a moment today, the full stamp for one from another day.

    A tray started this morning is the common case and wants four characters; one
    that has been up since Thursday is the interesting case and wants the date.
    """
    today = today or dt.date.today()
    return moment.strftime("%H:%M" if moment.date() == today else STAMP)


def format_stamp(version: str, code: dt.datetime | None, started: dt.datetime) -> str:
    """The build line, from values handed in. Pure, so the `code is None` case is reachable.

    :func:`describe` is the argument-free form every real caller wants. This one
    takes all three explicitly rather than defaulting them to the module
    constants, because `code or CODE_MTIME` would quietly turn a deliberate
    `None` back into the constant — and `None` is precisely the degradation this
    has to render.
    """
    if code is None:
        return f"{version} - started {_clock(started)}"
    return f"{version} - code {code.strftime(STAMP)}, started {_clock(started)}"


def describe() -> str:
    """The one line the tray menu shows: what code is running, and since when."""
    return format_stamp(__version__, CODE_MTIME, STARTED_AT)


def outdated(loaded: dt.datetime | None, now: dt.datetime | None = None) -> bool:
    """Whether the code on disk is newer than the `loaded` code a process reported.

    Deliberately not called `stale`: `referat status` already uses that word for a
    status file left behind by a tray that died, and the two would appear in the
    same few lines of output meaning different things.

    The caller supplies both sides because the useful comparison spans two
    processes: `loaded` comes out of a running tray's `status.json`, and `now` is
    read fresh by whoever is asking. Unknown on either side is not stale — this
    answers *no* rather than guessing.
    """
    if loaded is None:
        return False
    current = now or source_mtime()
    return current is not None and current > loaded
