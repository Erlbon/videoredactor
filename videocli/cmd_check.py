"""
videocli/cmd_check.py

  check PATH...   the Media > Check Files scan: can the file be opened, does it end early, is there a seek
                  index. --repair fixes the REPAIRABLE ones with a lossless remux; --stamp records the result
                  in the file like the window does.

A file is DAMAGED (its content is incomplete or unreadable), REPAIRABLE (a lossless remux fixes it), has a NOTE
(worth knowing) or is CHECKED OK. --repair never touches a DAMAGED file, since repairing it would drop its
unreadable end: that stays a decision for Media > Check Files in the app. Exit code 1 when a file is still
DAMAGED or REPAIRABLE after the command, could not be opened, or could not be checked (ffprobe missing).
"""

from __future__ import annotations

import argparse

from core.file_check import DAMAGED, REPAIRABLE, CheckResult, RepairError, quick_check, repair
from core.video_file import VideoFile
from core.video_fingerprint import video_fingerprint
from redactor_common.cli import EXIT_OK, EXIT_PARTIAL, Output, add_common_options
from redactor_common.cli.commands import trash_to
from redactor_common.core.trash import move_to_trash

from videocli.files import add_path_arguments, collect, load_videos

PROBLEM_STATUSES = {"DAMAGED", "REPAIRABLE"}


def add_check_parser(sub) -> None:
    parser = sub.add_parser(
        "check", help="check the video files for damage",
        description="Check each video: can it be opened, does it end early, is it seekable. Nothing is changed unless "
                    "--repair or --stamp.",
    )
    add_path_arguments(parser)
    parser.add_argument("--repair", action="store_true",
                        help="repair the REPAIRABLE files with a lossless remux (the original goes to the Recycle Bin or --trash-dir)")
    parser.add_argument("--stamp", action="store_true",
                        help="record the result in the file (the same scan stamp Media > Check Files writes)")
    parser.add_argument("--trash-dir", metavar="FOLDER", help="with --repair: move originals here instead of the Recycle Bin")
    parser.add_argument("-n", "--dry-run", action="store_true", help="with --repair or --stamp: show what would be done, change nothing")
    add_common_options(parser)
    parser.set_defaults(handler=run_check)


def _findings(result: CheckResult) -> list[dict]:
    return [{"code": f.code, "severity": f.severity, "message": f.message} for f in result.findings]


def run_check(args: argparse.Namespace, out: Output) -> int:
    files = collect(args.paths, out, recurse=not args.no_recurse)
    trash = trash_to(args.trash_dir) if args.trash_dir else move_to_trash
    problems = 0
    for index, video in enumerate(load_videos(files), start=1):
        path = str(video.path)
        out.progress(index, len(files), path)
        row = {
            "path": path, "status": "", "findings": [], "declared_seconds": None, "readable_seconds": None,
            "repaired": False, "stamped": False, "message": "", "problem": False,
        }
        if video.load_error:
            row["status"], row["message"], row["problem"] = "UNREADABLE", video.load_error, True
        else:
            result = quick_check(path)
            if any(f.code == "no_tool" for f in result.findings):
                row["status"], row["message"], row["problem"] = "NOT CHECKED", result.summary(), True
            else:
                result = _maybe_repair(path, result, args, trash, row)
                _maybe_stamp(video, result, args, row)
                row["status"] = result.status
                row["findings"] = _findings(result)
                row["declared_seconds"], row["readable_seconds"] = result.declared_seconds, result.readable_seconds
                row["problem"] = result.status in PROBLEM_STATUSES or not result.openable
        problems += row["problem"]
        out.record(row)
        out.line(f"{'PROBLEM' if row['problem'] else 'ok':8} {path}  [{row['status']}]")
        if row["message"]:
            out.line(f"         {row['message']}")
        for finding in row["findings"]:
            out.line(f"         {finding['severity']}: {finding['message']}")
        if row["repaired"]:
            out.line("         repaired (lossless remux)")
        if row["stamped"]:
            out.line("         result recorded in the file")
    out.finish({"files": len(files), "problems": problems, "repair": args.repair, "stamp": args.stamp, "dry_run": args.dry_run})
    return EXIT_PARTIAL if problems else EXIT_OK


def _maybe_repair(path: str, result: CheckResult, args, trash, row: dict) -> CheckResult:
    """The result after --repair (a REPAIRABLE file remuxed losslessly); anything else is left alone."""
    if not args.repair or result.status != "REPAIRABLE" or not result.can_repair:
        if args.repair and any(f.severity == DAMAGED for f in result.findings):
            row["message"] = "damaged: not repaired automatically (a repair would drop the unreadable end); use Media > Check Files"
        return result
    if args.dry_run:
        row["message"] = "would be repaired with a lossless remux"
        return result
    try:
        outcome = repair(path, result, trash=trash)
    except RepairError as exc:
        row["message"] = f"repair failed, the original is untouched: {exc}"
        return result
    row["repaired"] = True
    if outcome.note:
        row["message"] = outcome.note
    return outcome.check


def _maybe_stamp(video: VideoFile, result: CheckResult, args, row: dict) -> None:
    if not args.stamp or args.dry_run:
        return
    if video.record_check(result, video_fingerprint(str(video.path))):
        video.save()
        if video.save_error:
            row["message"] = f"the result could not be recorded: {video.save_error}"
        else:
            row["stamped"] = True
