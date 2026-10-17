"""
videocli/cmd_remux.py

  fix-container PATH...   files whose container is not what their extension says (an .mp4 that is really an FLV)
                          are remuxed losslessly into a real MP4/MKV. Without --apply it is a DRY RUN: it lists
                          what would be remuxed and writes nothing. With --apply the original is MOVED (never
                          deleted) to a "remuxed originals <date>" folder (Library safety's quarantine folder when
                          one is set, else a folder beside the file) and a UTF-8 TSV log is written.
  undo-remux LOG.tsv      reads such a log and puts every remuxed file back (the remuxed copy is moved to an
                          "undone remux <date>" folder beside it, never deleted).

Files modified in the last 10 minutes (Library safety setting) are skipped. Exit code 1 when a file failed or
could not be read; 0 otherwise (also for a dry run that found files to fix).
"""

from __future__ import annotations

import argparse

from core import container_check, container_remux
from redactor_common.cli import EXIT_OK, EXIT_PARTIAL, CliError, Output, add_common_options, expand_paths
from redactor_common.core.temp_names import is_app_temp_name


def add_fix_container_parser(sub) -> None:
    parser = sub.add_parser(
        "fix-container", help="remux files whose container does not match their extension",
        description="Find files whose real container is not what their extension says (an .mp4 that is really an "
                    "FLV) and remux them losslessly. Dry run unless --apply.",
    )
    parser.add_argument("paths", nargs="+", metavar="PATH", help="video files, folders or wildcards")
    parser.add_argument("-R", "--no-recurse", action="store_true", help="for a folder, look only at the files directly in it")
    parser.add_argument("--apply", action="store_true", help="really remux (the default is a dry run that writes nothing)")
    parser.add_argument("--log", metavar="FILE", help="with --apply: the TSV undo log to write (default: beside the quarantine folder)")
    add_common_options(parser)
    parser.set_defaults(handler=run_fix_container)


def add_undo_remux_parser(sub) -> None:
    parser = sub.add_parser(
        "undo-remux", help="undo the remuxes of a fix-container log",
        description="Put back the originals recorded in a TSV log written by fix-container --apply (or the Check Files "
                    "remux).",
    )
    parser.add_argument("log", metavar="LOG.tsv", help="the undo log")
    add_common_options(parser)
    parser.set_defaults(handler=run_undo_remux)


def run_fix_container(args: argparse.Namespace, out: Output) -> int:
    files, missing = expand_paths(
        args.paths, tuple(sorted(container_check.CHECKED_EXTENSIONS)), recursive=not args.no_recurse, skip=is_app_temp_name,
    )
    for argument in missing:
        out.error(f"nothing found for {argument}")
    if not files:
        raise CliError("no video files found")
    problems = 0
    rows = []

    def show(result: container_remux.RemuxResult) -> None:
        nonlocal problems
        problems += result.status == container_remux.FAILED
        rows.append({"path": result.path, "status": result.status, "message": result.message, "new_path": result.new_path,
                     "quarantined": result.quarantined})
        out.record(rows[-1])
        if result.status != container_remux.RIGHT:
            out.line(result.line())

    results, log_path = container_remux.remux_files(
        files, dry_run=not args.apply, log_path=args.log or "", on_result=show,
    )
    counts = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    if not args.apply:
        out.line("DRY RUN: nothing was changed or written.")
    elif log_path:
        out.line(f"Undo log: {log_path}")
    out.line(f"{len(files)} file(s) looked at: " + (", ".join(f"{n} {status}" for status, n in sorted(counts.items())) or "none"))
    out.finish({"files": len(files), "dry_run": not args.apply, "counts": counts, "log": log_path})
    return EXIT_PARTIAL if problems else EXIT_OK


def run_undo_remux(args: argparse.Namespace, out: Output) -> int:
    log = container_remux.RemuxLog(args.log)
    if not log.rows():
        raise CliError(f"{args.log} has no remux rows (is it the right file?)")
    lines, problems = container_remux.undo_remux_log(args.log)
    for line in lines:
        out.line(line)
    for problem in problems:
        out.error(problem)
    out.line(f"Undid {len(lines)} remux(es), {len(problems)} problem(s).")
    out.record({"undone": lines, "problems": problems})
    out.finish({"undone": len(lines), "problems": len(problems)})
    return EXIT_PARTIAL if problems else EXIT_OK
