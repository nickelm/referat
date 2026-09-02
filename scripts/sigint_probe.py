r"""A 30-second stand-in for a model load, to find out what interrupts it.

Three `referat rerun` runs launched from the VS Code integrated terminal died
about six seconds in with `KeyboardInterrupt`, inside
`ctranslate2.models.Whisper(...)`, while the same command from an ordinary shell
ran to completion. Two explanations survive the log: the terminal sends
something on its own, or a person pressed Ctrl+C at a prompt that had printed
nothing for six seconds and looked hung.

This tells them apart without spending three minutes and a GPU on it. Run it in
the VS Code terminal and again in a plain PowerShell window, touch nothing while
it counts, and compare:

    .venv\Scripts\python.exe scripts\sigint_probe.py

An interrupt with nobody at the keyboard is the terminal. No interrupt in either
place, and the six-second deaths were a hand on Ctrl+C -- which is worth knowing
too, because then there is nothing to fix.

It prints a heartbeat a Whisper load does not, which is itself half the problem:
`load_model` says "loading" and then nothing at all until the model is up, and
silence is what a hang looks like.
"""

from __future__ import annotations

import logging
import time

from referat.cli import log_interrupts

SECONDS = 30

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    log_interrupts()
    print(f"Counting to {SECONDS}. Do not touch the keyboard. Ctrl+C is what we are hunting.")
    started = time.monotonic()
    try:
        for i in range(1, SECONDS + 1):
            time.sleep(1)
            print(f"  {i:2d}s", flush=True)
    except KeyboardInterrupt:
        print(f"\nINTERRUPTED after {time.monotonic() - started:.1f}s.")
        print("If your hands were off the keyboard, the terminal did this.")
        raise SystemExit(130)
    print("Finished untouched. Nothing in this terminal interrupts a long command.")
