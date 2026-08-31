"""The `referat` command line interface.

`config` since build step 1, `label` since 7b, `list`, `rerun` and `status`
since step 8, `devices` since the first USB microphone, and `index` with the
meetings folder scaffold at step 10. Between them they are the whole of Referat
that is not the tray: what has been recorded, what still needs a name, what the
tray is doing, what it will record with, how to transcribe a meeting again, and
how to rebuild the dashboard over all of it.

**Light by default.** Only `rerun` needs the `transcribe` extra, and it imports
it inside :func:`referat.rerun.run`, so `list` and `status` answer instantly
without three gigabytes of torch. `label` and `devices` are imported here rather
than at module scope for the same kind of reason: a broken sound device should
not be able to cost the rest of the CLI.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from dataclasses import asdict
from types import ModuleType

from referat import __version__, index, status, voices
from referat.config import Config, ConfigError, load_config
from referat.meeting import Meeting, format_duration, load_meetings

NEEDS_CONFIG = ("config", "devices", "index", "label", "list", "rerun")
"""`status` is missing from this on purpose: `status.json` lives in
`%LOCALAPPDATA%`, so an unusable `config.toml` must not stop you asking what the
tray is doing — that is exactly the moment you want to ask."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="referat",
        description="Personal offline meeting recorder and transcriber.",
    )
    parser.add_argument("--version", action="version", version=f"referat {__version__}")

    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser("config", help="show the loaded configuration")
    subcommands.add_parser(
        "list",
        help="every meeting: duration, status, and who still needs a name",
        description=(
            "List the meetings folder, oldest first. The UNNAMED column is the "
            "queue for `referat label`; AUDIO says whether the WAVs are still on "
            "disk or were released once the transcript came out clean."
        ),
    )
    subcommands.add_parser(
        "status",
        help="what the tray app is doing right now",
        description=(
            "Read the status file the tray writes on every transition. Exit code "
            "0 when a tray is running, 1 when none is."
        ),
    )

    subcommands.add_parser(
        "index",
        help="regenerate the meetings folder's INDEX.md dashboard",
        description=(
            "Rewrite <meetings_dir>/INDEX.md: one row per meeting, with the title "
            "taken from the H1 of its notes.md. It is written automatically at the "
            "end of every transcription, so this is for after a /cleanup run has "
            "given a meeting its title. A different file from the repository's own "
            "INDEX.md, and never hand-edited."
        ),
    )

    subcommands.add_parser(
        "devices",
        help="list the audio devices, and which ones [audio] selects",
        description=(
            "Every input device Windows exposes and every WASAPI loopback source, "
            "with the one `[audio].mic_device` and `[audio].loopback_device` "
            "currently resolve to marked `<- config`. Those two keys are substring "
            "matches, and a substring that matches nothing falls back to the "
            "default silently, so this is how you check that the device you meant "
            "is the device that will be recorded."
        ),
    )

    rerun = subcommands.add_parser(
        "rerun",
        help="transcribe a meeting again from the audio it kept",
        description=(
            "Re-run transcription and diarization over a meeting's WAVs, writing "
            "a new transcript.md. Speakers are renumbered and re-identified from "
            "the known-voices database, so names survive but SPEAKER_NN numbers "
            "do not. Needs the `transcribe` extra."
        ),
    )
    rerun.add_argument("meeting_id", help="e.g. 2026-08-27_1400")
    rerun.add_argument(
        "--force",
        action="store_true",
        help="run even while the tray app is transcribing, which means two "
        "models on one GPU",
    )

    label = subcommands.add_parser(
        "label",
        help="name the speakers identification could not place",
        description=(
            "Play each unidentified speaker's snippets and ask who it was, then "
            "remember that voice for every meeting after this one. With no "
            "meeting id, walks every meeting that still has unknown speakers, "
            "oldest first."
        ),
    )
    label.add_argument("meeting_id", nargs="?", help="a single meeting, e.g. 2026-08-27_1400")
    label.add_argument(
        "--forget",
        metavar="NAME",
        help="delete this person from the known-voices database and revert their "
        "labels to SPEAKER_NN in every transcript",
    )

    return parser


# --- referat list -----------------------------------------------------------


def audio_state(meeting: Meeting) -> str:
    """`released`, `kept`, or `-` for a meeting that never recorded a channel."""
    if meeting.transcription.get("audio_released"):
        return "released"
    if meeting.mic_path.exists() or meeting.system_path.exists():
        return "kept"
    return "-"


def list_row(meeting: Meeting) -> tuple[str, str, str, str, str]:
    """One line of `referat list`, as its five columns."""
    unnamed = len(voices.unknown_speakers(meeting))
    return (
        meeting.id,
        format_duration(meeting.duration_seconds),
        str(meeting.status),
        audio_state(meeting),
        str(unnamed) if unnamed else "-",
    )


HEADERS = ("MEETING", "DURATION", "STATUS", "AUDIO", "UNNAMED")
RIGHT_ALIGNED = (1, 4)
"""Duration and the unnamed count read as numbers; the rest read as words."""


def render_table(
    rows: list[tuple[str, ...]],
    headers: tuple[str, ...],
    right_aligned: tuple[int, ...] = (),
) -> str:
    """A plain aligned table. ASCII only — the console code page here is not UTF-8."""
    widths = [max(len(str(cell)) for cell in column) for column in zip(headers, *rows)]
    lines = []
    for row in (headers, *rows):
        cells = [
            str(cell).rjust(width) if index in right_aligned else str(cell).ljust(width)
            for index, (cell, width) in enumerate(zip(row, widths))
        ]
        lines.append("  ".join(cells).rstrip())
    return "\n".join(lines)


def run_list(config: Config) -> int:
    """`referat list`. Oldest first, so the newest meeting lands next to the prompt."""
    meetings = load_meetings(config)
    if not meetings:
        print(f"No meetings in {config.paths.meetings_dir} yet.")
        return 0

    rows = [list_row(m) for m in meetings]
    print(render_table(rows, HEADERS, RIGHT_ALIGNED))

    pending = sum(1 for row in rows if row[4] != "-")
    summary = f"\n{len(meetings)} meeting{'s' if len(meetings) != 1 else ''}"
    if pending:
        summary += f", {pending} with unnamed speakers - run: referat label"
    staged = sum(1 for m in meetings if m.dir.parent != config.paths.meetings_dir)
    if staged:
        # Still in staging means the audio was kept, so the transcript was not
        # trusted. Say so, or these read as identical to the finished ones.
        summary += f", {staged} still local with audio kept - run: referat rerun"
    print(summary)
    return 0


# --- referat status ---------------------------------------------------------


def run_status() -> int:
    """`referat status`. Exit 0 when a tray is running, 1 when none is.

    A status file whose pid is dead is reported rather than hidden: it is the
    trace a crashed or killed tray leaves behind, and knowing it died while
    transcribing is worth more than a bare "not running".
    """
    current = status.read_status()
    if current is None:
        print("Referat is not running.")
        return 1
    if not status.is_running(current.pid):
        print(
            f"Referat is not running.\n"
            f"stale status file from {current.updated_at} "
            f"(state: {current.state}, pid {current.pid})"
        )
        return 1

    line = str(current.state)
    if current.meeting_id:
        line += f"  {current.meeting_id}"
        elapsed = _elapsed(current.started_at)
        if elapsed:
            line += f"  {elapsed}"
    print(line)
    jobs = f", {current.jobs} transcription job{'s' if current.jobs != 1 else ''}"
    print(f"tray pid {current.pid}{jobs if current.jobs else ''}")
    print(f"updated {current.updated_at}")
    return 0


def _elapsed(started_at: str | None) -> str:
    """How long the current meeting has been going, or "" when it cannot be told."""
    if not started_at:
        return ""
    try:
        started = dt.datetime.fromisoformat(started_at)
    except ValueError:
        return ""
    return f"{format_duration((dt.datetime.now() - started).total_seconds())} so far"


# --- referat devices --------------------------------------------------------


DEVICE_HEADERS = ("INDEX", "NAME", "HOSTAPI", "CH", "RATE", "")
LOOPBACK_HEADERS = ("NAME", "CH", "RATE", "")


def _marker(selected: bool, default: bool) -> str:
    """The right-hand column: what the config picked, what Windows would pick."""
    parts = []
    if selected:
        parts.append("<- config")
    if default:
        parts.append("(default)")
    return " ".join(parts)


def _setting(key: str, value: str) -> str:
    """`[audio].mic_device = "Jabra"`, saying so when it is empty."""
    suffix = "" if value else "  (system default)"
    return f'[audio].{key} = "{value}"{suffix}'


def _input_section(sd: ModuleType, recorder: ModuleType, wanted: str) -> str:
    """The input devices, with the one `mic_device` resolves to marked."""
    lines = [_setting("mic_device", wanted)]

    # The recorder's own resolver, not a copy of its rules: a listing that
    # disagreed with what actually gets recorded would be worse than none.
    selected = recorder.resolve_mic_device(wanted)
    try:
        devices = list(sd.query_devices())
        hostapis = list(sd.query_hostapis())
    except Exception as exc:
        return "\n".join([*lines, f"could not enumerate input devices: {exc}"])

    try:
        default = sd.default.device[0]
    except Exception:
        default = -1

    rows: list[tuple[str, ...]] = []
    for index, device in enumerate(devices):
        if not device["max_input_channels"]:
            continue
        rows.append(
            (
                str(index),
                str(device["name"]),
                str(hostapis[device["hostapi"]]["name"]),
                str(device["max_input_channels"]),
                str(int(device["default_samplerate"])),
                _marker(index == selected, index == default),
            )
        )

    if wanted.strip() and selected is None:
        lines.append(f"nothing matches {wanted!r} - the default input will be recorded")
    if not rows:
        lines.append("no input devices")
        return "\n".join(lines)
    lines.append("")
    lines.append(render_table(rows, DEVICE_HEADERS, (0, 3, 4)))
    return "\n".join(lines)


def _loopback_section(recorder: ModuleType, wanted: str) -> str:
    """The WASAPI loopback sources, with the one `loopback_device` resolves to marked.

    Deliberately without an index column: PyAudio numbers its devices in its own
    namespace, and printing that next to sounddevice's would invite setting one
    from the other.
    """
    import pyaudiowpatch as pyaudio

    lines = [_setting("loopback_device", wanted)]
    audio = pyaudio.PyAudio()
    try:
        try:
            selected = recorder.resolve_loopback_device(audio, wanted)
        except recorder.RecorderError as exc:
            return "\n".join([*lines, f"no loopback available: {exc}"])
        # `_matches` rather than a substring test written out again here: the
        # question is whether the *recorder* considers this a match. Unlike the
        # mic resolver, this one has no way of saying "I fell back" — it returns
        # the default output's loopback either way — so ask afterwards.
        matched = bool(wanted.strip()) and recorder._matches(str(selected["name"]), wanted)
        if wanted.strip() and not matched:
            lines.append(f"nothing matches {wanted!r} - the default output will be recorded")

        rows: list[tuple[str, ...]] = []
        for info in audio.get_loopback_device_info_generator():
            resolved = info["index"] == selected["index"]
            rows.append(
                (
                    str(info["name"]),
                    str(info["maxInputChannels"]),
                    str(int(info["defaultSampleRate"])),
                    _marker(resolved and matched, resolved and not matched),
                )
            )
    finally:
        audio.terminate()

    if not rows:
        lines.append("no loopback devices")
        return "\n".join(lines)
    lines.append("")
    lines.append(render_table(rows, LOOPBACK_HEADERS, (1, 2)))
    return "\n".join(lines)


def run_devices(config: Config) -> int:
    """`referat devices`. What is plugged in, and what `[audio]` does with it."""
    import sounddevice as sd

    from referat import recorder

    print(_input_section(sd, recorder, config.audio.mic_device))
    print()
    print(_loopback_section(recorder, config.audio.loopback_device))
    return 0


# --- Entry point ------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    if args.command == "status":
        return run_status()

    try:
        config = load_config() if args.command in NEEDS_CONFIG else None
    except ConfigError as exc:
        print(f"referat: {exc}", file=sys.stderr)
        return 1
    assert config is not None

    if args.command == "config":
        print(f"source: {config.source}")
        for section, values in asdict(config).items():
            if isinstance(values, dict):
                print(f"[{section}]")
                for key, value in values.items():
                    print(f"  {key} = {value}")
        return 0

    if args.command == "list":
        return run_list(config)

    if args.command == "index":
        return index.run(config)

    if args.command == "devices":
        return run_devices(config)

    if args.command == "label":
        # Imported here rather than at module scope so `referat --version` and
        # `referat config` stay instant, and so a broken sound device cannot
        # cost the rest of the CLI.
        from referat import label as labelling

        if args.forget:
            return labelling.run_forget(config, args.forget)
        return labelling.run(config, args.meeting_id)

    if args.command == "rerun":
        # And this one keeps the `transcribe` extra out of every other command.
        from referat import rerun as rerunning

        return rerunning.run(config, args.meeting_id, force=args.force)

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
