"""Global hotkeys, the only way to drive Referat while another window has focus.

The two combos come from `[hotkeys]` in `config.toml` and are what the
programmable USB buttons are set to emit. The `keyboard` library installs a
low-level Windows hook and calls back on its own thread, so the callbacks handed
in here must be quick and thread-safe.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

import keyboard

from referat.config import HotkeysConfig

log = logging.getLogger(__name__)

DEBOUNCE_SECONDS = 0.4
"""Ignore a repeat of the same combo within this window.

Holding a key down auto-repeats, and the USB buttons are no different; without
this a slightly long press would start and immediately stop a recording.
"""


class Hotkeys:
    """Registers the two configured combos for as long as it is started."""

    def __init__(
        self,
        config: HotkeysConfig,
        *,
        on_toggle_record: Callable[[], None],
        on_toggle_pause: Callable[[], None],
    ) -> None:
        self._config = config
        self._callbacks = {
            config.toggle_record: on_toggle_record,
            config.toggle_pause: on_toggle_pause,
        }
        self._handles: list[object] = []
        self._last_fired: dict[str, float] = {}
        self._lock = threading.Lock()

    def start(self) -> None:
        """Register both combos.

        A combo that will not register is logged and skipped rather than raising:
        a bad `config.toml` entry, or a hook Windows refuses to install, should
        leave the tray menu usable and say why in the log.
        """
        for combo, callback in self._callbacks.items():
            try:
                handle = keyboard.add_hotkey(
                    combo,
                    self._fire,
                    args=(combo, callback),
                    suppress=False,
                    trigger_on_release=False,
                )
            except Exception:
                log.exception("could not register hotkey %r", combo)
                continue
            self._handles.append(handle)
            log.info("hotkey registered: %s", combo)

    def stop(self) -> None:
        """Unregister everything this instance registered."""
        for handle in self._handles:
            try:
                keyboard.remove_hotkey(handle)
            except (KeyError, ValueError):
                pass  # Already gone; nothing to undo.
        self._handles.clear()
        log.info("hotkeys unregistered")

    def _fire(self, combo: str, callback: Callable[[], None]) -> None:
        """Debounce, then run the callback on the keyboard hook thread."""
        now = time.monotonic()
        with self._lock:
            if now - self._last_fired.get(combo, 0.0) < DEBOUNCE_SECONDS:
                log.debug("debounced %s", combo)
                return
            self._last_fired[combo] = now
        log.debug("hotkey fired: %s", combo)
        try:
            callback()
        except Exception:
            # The hook thread must survive; a failed action is logged, not fatal.
            log.exception("hotkey %r handler failed", combo)
