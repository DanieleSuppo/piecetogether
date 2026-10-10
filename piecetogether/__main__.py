"""Local development service; one normalized JSON Communication per line."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from .application_api import ApplicationApiServer
from .core import Bootstrap, Core
from .email_channel import EmailAcquisitionWorker, EmailChannel, ImapTransport, MAX_EMAIL_BYTES

MAX_LINE = 65536


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config/development.json"))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--inspect", metavar="COMMUNICATION_ID", help="local operator inspection"
    )
    mode.add_argument(
        "--serve-api", action="store_true", help="serve the configured application API"
    )
    mode.add_argument('--receive-email', action='store_true', help='receive one MIME Email from stdin')
    mode.add_argument('--serve-email', action='store_true', help='poll the configured IMAP mailbox')
    args = parser.parse_args()
    try:
        core = Core(Bootstrap.from_file(args.config))
    except (ValueError, TypeError, RecursionError, OSError, sqlite3.Error):
        print("Invalid or unavailable deployment configuration/state.", file=sys.stderr)
        return 1
    if args.inspect:
        try:
            print(json.dumps(core.inspect(args.inspect)))
        except (KeyError, sqlite3.Error):
            print("Communication unavailable.", file=sys.stderr)
            return 1
        return 0
    if args.serve_api:
        try:
            ApplicationApiServer(core).serve_forever()
        except (ValueError, OSError, sqlite3.Error):
            print("Invalid or unavailable application API configuration/state.", file=sys.stderr)
            return 1
        return 0
    if args.serve_email:
        if core.config.channel != 'email' or core.config.imap is None or not isinstance(core.channel, EmailChannel):
            print('Email IMAP acquisition is not configured.', file=sys.stderr)
            return 1
        try:
            EmailAcquisitionWorker(core, core.channel,
                                   ImapTransport(core.config.imap, core.config.secret_references,
                                                 core.channel.acquired),
                                   core.config.imap).run()
        except (ValueError, TypeError, RecursionError, OSError, sqlite3.Error):
            print('Invalid or unavailable Email acquisition configuration/state.', file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            return 0
        return 0
    if args.receive_email:
        if core.config.channel != 'email':
            print('Email channel is not configured.', file=sys.stderr)
            return 1
        try:
            result = core.accept_transport(sys.stdin.buffer.read(MAX_EMAIL_BYTES + 1))
        except (ValueError, TypeError, RecursionError):
            result = {'error': 'invalid_communication'}
        except (OSError, sqlite3.Error):
            result = {'error': 'storage_unavailable'}
        print(json.dumps(result), flush=True)
        return 0
    if core.config.channel == 'email':
        print('Email acquisition requires --receive-email.', file=sys.stderr)
        return 1
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
            result = core.accept_transport(json.loads(line))
        except (ValueError, TypeError, RecursionError):
            result = {"error": "invalid_communication"}
        except (OSError, sqlite3.Error):
            result = {"error": "storage_unavailable"}
        print(json.dumps(result), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
