"""Logging configuration shared by the tray app and the CLI.

The tray app runs under pythonw.exe with no console, so the rotating file in
`%LOCALAPPDATA%\\Referat` is the only record of what happened during a meeting.
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from referat import paths

FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
MAX_BYTES = 2_000_000
BACKUP_COUNT = 3


def setup_logging(level: str = "INFO", *, console: bool = True) -> Path:
    """Install the rotating file handler (and optionally a console one).

    Returns the log file path. Safe to call more than once; handlers installed
    by a previous call are replaced rather than duplicated.
    """
    log_file = paths.log_path()
    log_file.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter(FORMAT, datefmt=DATE_FORMAT)

    file_handler = RotatingFileHandler(
        log_file, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    # Under pythonw.exe there is no usable stderr, so only attach a console
    # handler when one actually exists.
    if console and sys.stderr is not None:
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(formatter)
        root.addHandler(stream_handler)

    return log_file
