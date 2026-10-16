"""
videocli/cmd_lookup.py

  lookup-check theporndb|stashdb   one cheap authenticated request to the service with the saved key (or the
                                   THEPORNDB_API_KEY / STASHDB_API_KEY environment variable), to see on this computer
                                   whether the key works. Prints OK, or the exact reason (no key, HTTP 401, the server
                                   unreachable, ...). Exit code 0 when the key was accepted, 1 otherwise.

The StashDB address is the one set under Tools > API Keys (default https://stashdb.org/graphql); --endpoint tries
another stash-box server for this run only. ThePornDB's check is GET /auth/user, StashDB's is the GraphQL query
`me { name }`. Nothing about any file is sent. The lookups themselves are Metadata > Look Up in the app and the
"Fill from ThePornDB/StashDB" Redact step (`videoredactor redact --enable scene_lookup`).
"""

from __future__ import annotations

import argparse

from core import scene_lookup, stashdb_client, theporndb_client
from core.scene_lookup import SceneLookupError
from redactor_common.cli import EXIT_OK, EXIT_PARTIAL, CliError, Output, add_common_options

SERVICES = ("theporndb", "stashdb")


def add_lookup_check_parser(sub) -> None:
    parser = sub.add_parser(
        "lookup-check", help="check the ThePornDB or StashDB key",
        description="Make one small authenticated request to ThePornDB or StashDB with the saved key and say whether it "
                    "was accepted (OK) or the exact reason it was not.",
    )
    parser.add_argument("service", choices=SERVICES, help="which service to check")
    parser.add_argument("--endpoint", metavar="URL", help="StashDB only: another stash-box address, for this run")
    add_common_options(parser)
    parser.set_defaults(handler=run_lookup_check)


def run_lookup_check(args: argparse.Namespace, out: Output) -> int:
    try:
        if args.service == "theporndb":
            if args.endpoint:
                raise CliError("--endpoint is for stashdb only")
            call = theporndb_client.check_key
        else:
            endpoint = scene_lookup.normalize_endpoint(args.endpoint) if args.endpoint else None
            call = lambda: stashdb_client.check_key(endpoint=endpoint)  # noqa: E731
    except ValueError as exc:
        raise CliError(str(exc)) from None
    try:
        message, ok = call(), True
    except SceneLookupError as exc:
        message, ok = f"FAILED: {exc}", False
    out.record({"service": args.service, "ok": ok, "message": message})
    out.line(message)
    out.finish({"service": args.service, "ok": ok})
    return EXIT_OK if ok else EXIT_PARTIAL
