"""
videocli/cmd_join.py

  join-files PATH...   the parts of a movie (Title cd1.mp4, Title cd2.mp4; also disc / part / pt) are joined into one
                       file without re-encoding. Without --apply it is a DRY RUN: it lists what would be joined and
                       why anything cannot be, and writes nothing. With --apply every part is MOVED (never deleted)
                       to a "joined originals <date>" folder (the Library safety quarantine folder when one is set,
                       else a folder beside the file), the tags and poster come from the first part, and a UTF-8 TSV
                       log is written.
  undo-join LOG.tsv    reads such a log and puts every part back (the joined file is moved to an "undone join <date>"
                       folder beside it, never deleted).

Parts modified in the last 10 minutes (Library safety setting) are skipped. Exit code 1 when a group failed or
could not be joined; 0 otherwise (also for a dry run).
"""

from __future__ import annotations

import argparse

from core import join_files
from core.container_remux import RemuxLog
from redactor_common.cli import EXIT_OK, EXIT_PARTIAL, CliError, Output, add_common_options, expand_paths
from redactor_common.core.temp_names import is_app_temp_name

JOIN_EXTENSIONS = (".mp4", ".mkv", ".m4v")


def add_join_files_parser(sub) -> None:
    parser = sub.add_parser(
        "join-files", help="join the parts of a movie (cd1, cd2, ...) into one file without re-encoding",
        description="Join files that are parts of one movie into a single file, losslessly. Files are grouped by name "
                    "and part number; a group whose parts differ (codec, size, frame rate, tracks) is reported and "
                    "left alone. Dry run unless --apply.",
    )
    parser.add_argument("paths", nargs="+", metavar="PATH", help="the part files, folders or wildcards")
    parser.add_argument("-R", "--no-recurse", action="store_true", help="for a folder, look only at the files directly in it")
    parser.add_argument("--apply", action="store_true", help="really join (the default is a dry run that writes nothing)")
    parser.add_argument("--chapters", action="store_true", help="add one chapter per part (Part 1, Part 2, ...)")
    parser.add_argument("--log", metavar="FILE", help="with --apply: the TSV undo log to write (default: beside the quarantine folder)")
    add_common_options(parser)
    parser.set_defaults(handler=run_join_files)


def add_undo_join_parser(sub) -> None:
    parser = sub.add_parser(
        "undo-join", help="undo the joins of a join-files log",
        description="Put back the parts recorded in a TSV log written by join-files --apply (or the Join Files window).",
    )
    parser.add_argument("log", metavar="LOG.tsv", help="the undo log")
    add_common_options(parser)
    parser.set_defaults(handler=run_undo_join)


def run_join_files(args: argparse.Namespace, out: Output) -> int:
    files, missing = expand_paths(args.paths, JOIN_EXTENSIONS, recursive=not args.no_recurse, skip=is_app_temp_name)
    for argument in missing:
        out.error(f"nothing found for {argument}")
    if not files:
        raise CliError("no video files found")
    groups = join_files.group_files(files)
    problems = 0

    def show(result: join_files.JoinResult) -> None:
        nonlocal problems
        problems += result.status in (join_files.FAILED, join_files.SKIPPED)
        out.record({"name": result.name, "status": result.status, "message": result.message,
                    "new_path": result.new_path, "quarantined": result.quarantined})
        out.line(result.line())

    results, log_path = join_files.join_groups(
        groups, dry_run=not args.apply, chapters=args.chapters, log_path=args.log or "", on_result=show,
    )
    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    if not args.apply:
        out.line("DRY RUN: nothing was changed or written.")
    elif log_path:
        out.line(f"Undo log: {log_path}")
    out.line(f"{len(groups)} movie(s) from {len(files)} file(s): "
             + (", ".join(f"{n} {s}" for s, n in sorted(counts.items())) or "none"))
    out.finish({"files": len(files), "groups": len(groups), "dry_run": not args.apply, "counts": counts, "log": log_path})
    return EXIT_PARTIAL if problems else EXIT_OK


def run_undo_join(args: argparse.Namespace, out: Output) -> int:
    if not RemuxLog(args.log).rows():
        raise CliError(f"{args.log} has no join rows (is it the right file?)")
    lines, problems = join_files.undo_join_log(args.log)
    for line in lines:
        out.line(line)
    for problem in problems:
        out.error(problem)
    out.line(f"Put back {len(lines)} part(s), {len(problems)} problem(s).")
    out.record({"restored": lines, "problems": problems})
    out.finish({"restored": len(lines), "problems": len(problems)})
    return EXIT_PARTIAL if problems else EXIT_OK
