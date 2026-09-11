"""Listen for the lid and for sleep, and print what Windows delivers.

The tray stops a meeting when the lid closes or the machine suspends
(:class:`referat.power.PowerEvents`), and nothing in an automated session can
close a lid. This registers the same two notifications the tray does and prints
every event for a while, so a person can close the lid, put the machine to
sleep, or do nothing, and see what arrived - which is the only way to measure
whether *this* laptop delivers `PBT_APMSUSPEND` on a lid close under Modern
Standby, or only the lid switch, or neither.

Two things are checked without anybody touching anything: both registrations
succeed, and the lid switch reports its current state on registration - which
is why :class:`PowerEvents` ignores the first report rather than treating it as
a change.

    .venv\\Scripts\\python.exe scripts\\power_probe.py [seconds]

Diagnostic, not part of the product. Exits non-zero if a registration failed.
"""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from referat import power  # noqa: E402


def main(argv: list[str]) -> int:
    seconds = float(argv[1]) if len(argv) > 1 else 30.0
    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)-7s %(message)s")
    events: list[str] = []

    def note(what: str) -> None:
        events.append(what)
        print(f"  event: {what}")

    listener = power.PowerEvents(
        on_lid_closed=lambda: note("lid closed"),
        on_suspend=lambda: note("suspend"),
        on_resume=lambda: note("resume"),
    )
    if not listener.registered:
        print("MISS neither registration succeeded")
        return 1
    time.sleep(0.5)
    state = {power.LID_OPEN: "open", power.LID_CLOSED: "closed", None: "not reported"}[listener.lid]
    print(f"ok   registered; the lid is currently {state}")
    print(f"     listening for {seconds:.0f}s - close the lid or choose Sleep to see what arrives")
    deadline = time.time() + seconds
    while time.time() < deadline:
        time.sleep(1.0)
    listener.close()
    print(f"done {len(events)} event(s): {', '.join(events) or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
