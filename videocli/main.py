"""
videocli/main.py

The videoredactor command line, run through the app's own exe (main.py dispatches here when its first
argument is a command; the window never starts):

    videoredactor info    PATH...                      what the videos are
    videoredactor set     PATH... -s show_title=Dune   change tag fields
    videoredactor rename  PATH... -p "%show_title% - S%season_number%E%episode_number% - %title%"
    videoredactor move    PATH... -p "%show_title%/Season %season_number%/..." --root LIBRARY
    videoredactor check   PATH... [--repair] [--stamp] check for damage, optionally repair
    videoredactor redact  PATH...                      run the saved Redact recipe

Every command takes --json (one JSON document), --quiet and --output FILE (the result goes to a file: the
reliable way to read it from a script, since a windowed exe cannot be waited for by an interactive shell),
and the ones that change files take --dry-run. Exit codes: 0 done, 1 some files failed or have problems,
2 bad arguments, 70 internal error, 130 interrupted. It reads the same settings file as the app.
"""

from __future__ import annotations

import argparse
from typing import Sequence

from core.version import APP_VERSION
from redactor_common.cli import make_output

from videocli import COMMANDS, cmd_check, cmd_files, cmd_redact, cmd_tags

PROG = "videoredactor"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG, description="The Video Redactor on the command line: inspect, tag, rename, check and redact video files.",
    )
    parser.add_argument("--version", action="version", version=f"{PROG} {APP_VERSION}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True
    cmd_tags.add_info_parser(sub)
    cmd_tags.add_set_parser(sub)
    cmd_files.add_rename_parser(sub)
    cmd_files.add_move_parser(sub)
    cmd_check.add_check_parser(sub)
    cmd_redact.add_redact_parser(sub)
    assert tuple(sub.choices) == COMMANDS, "videocli.COMMANDS must list the subcommands"
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out = make_output(args)
    try:
        return args.handler(args, out)
    finally:
        out.close()
