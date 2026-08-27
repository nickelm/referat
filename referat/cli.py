"""The `referat` command line interface.

Skeleton only at build step 1: enough to prove the entry point resolves and to
inspect the loaded config. The `list`, `rerun` and `status` subcommands land at
build step 8.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import asdict

from referat import __version__
from referat.config import ConfigError, load_config

NOT_YET = "not implemented yet (build step 8)"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="referat",
        description="Personal offline meeting recorder and transcriber.",
    )
    parser.add_argument("--version", action="version", version=f"referat {__version__}")

    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser("config", help="show the loaded configuration")
    subcommands.add_parser("list", help=f"list meetings — {NOT_YET}")
    subcommands.add_parser("status", help=f"what the tray app is doing — {NOT_YET}")
    rerun = subcommands.add_parser("rerun", help=f"re-run transcription — {NOT_YET}")
    rerun.add_argument("meeting_id", nargs="?")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        return 0

    if args.command == "config":
        try:
            config = load_config()
        except ConfigError as exc:
            print(f"referat: {exc}", file=sys.stderr)
            return 1
        print(f"source: {config.source}")
        for section, values in asdict(config).items():
            if isinstance(values, dict):
                print(f"[{section}]")
                for key, value in values.items():
                    print(f"  {key} = {value}")
        return 0

    print(f"referat {args.command}: {NOT_YET}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
