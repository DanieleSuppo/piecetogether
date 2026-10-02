"""Local development service; one normalized JSON Communication per line."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from .core import Bootstrap, Core

MAX_LINE = 65536


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/development.json"))
    parser.add_argument(
        "--inspect", metavar="COMMUNICATION_ID", help="local operator inspection"
    )
    args = parser.parse_args()
    try:
        core = Core(Bootstrap.from_file(args.config))
    except (ValueError, TypeError, OSError, sqlite3.Error):
        print("Invalid or unavailable deployment configuration/state.", file=sys.stderr)
        return 1
    if args.inspect:
        try:
            print(json.dumps(core.inspect(args.inspect)))
        except (KeyError, sqlite3.Error):
            print("Communication unavailable.", file=sys.stderr)
            return 1
        return 0
    # ponytail: one local JSONL worker; add a work scheduler for concurrent transports.
    while True:
        line = sys.stdin.readline(MAX_LINE + 1)
        if not line:
            break
        try:
            if len(line) > MAX_LINE:
                while not line.endswith("\n"):
                    line = sys.stdin.readline(MAX_LINE + 1)
                    if not line:
                        break
                raise ValueError("input line is too large")
            result = core.accept(json.loads(line))
        except (ValueError, TypeError, RecursionError):
            result = {"error": "invalid_communication"}
        except (OSError, sqlite3.Error):
            result = {"error": "storage_unavailable"}
        print(json.dumps(result), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
