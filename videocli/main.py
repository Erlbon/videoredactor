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
    videoredactor notes   PATH...  [--write]           draft series.md / about.md folder notes (TMDB + Wikipedia)
    videoredactor fix-container PATH... [--apply]      remux files whose container is not their extension (dry run unless --apply)
    videoredactor undo-remux LOG.tsv                   put the originals of such a remux back
    videoredactor join-files PATH... [--apply] [--chapters]   join the parts of a movie (cd1, cd2) without re-encoding (dry run unless --apply)
    videoredactor undo-join LOG.tsv                    put the parts of such a join back
    videoredactor set-language PATH... --audio eng [--subtitle nor] [--only-unlabelled]   set track languages in place (--dry-run shows)
    videoredactor lookup-check theporndb|stashdb       does the saved ThePornDB / StashDB key work? (OK or the reason)

Every command takes --json (one JSON document), --quiet and --output FILE (the result goes to a file: the
reliable way to read it from a script, since a windowed exe cannot be waited for by an interactive shell),
and the ones that change files take --dry-run. Exit codes: 0 done, 1 some files failed or have problems,
2 bad arguments, 70 internal error, 130 interrupted. It reads the same settings file as the app.
"""

from __future__ import annotations

from core.version import APP_VERSION
from redactor_common.cli.main_factory import make_main

from videocli import COMMANDS, cmd_check, cmd_clear, cmd_files, cmd_join, cmd_lookup, cmd_notes, cmd_language, cmd_redact, cmd_remux, cmd_tags

PROG = "videoredactor"

build_parser, main = make_main(
    PROG, APP_VERSION,
    "The Video Redactor on the command line: inspect, tag, rename, check and redact video files.",
    [cmd_tags.add_info_parser, cmd_tags.add_set_parser, cmd_files.add_rename_parser,
     cmd_files.add_move_parser, cmd_check.add_check_parser, cmd_redact.add_redact_parser, cmd_notes.add_notes_parser,
     cmd_lookup.add_lookup_check_parser, cmd_clear.add_clear_parser,
     cmd_remux.add_fix_container_parser, cmd_remux.add_undo_remux_parser, cmd_language.add_set_language_parser,
     cmd_join.add_join_files_parser, cmd_join.add_undo_join_parser],
    commands=COMMANDS,
)
