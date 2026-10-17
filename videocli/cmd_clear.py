"""
videocli/cmd_clear.py

  clear PATH... [--groups descriptive,sources,poster,type] [-n] [--yes]

Empties groups of fields on the video files and saves them in place, with the same plan code as Edit > Clear Fields (core/
clear_plan.py): descriptive fields, source ids and links, the embedded poster, and (only when named) the content type.
Never the scan stamp, the technical fields, the retired track title, subtitle files or the MP4 media type atom. Without
--yes (and without --dry-run) it refuses to run: there is no question to answer on a command line, and a cleared file
cannot be undone once saved.
"""

from __future__ import annotations

import argparse

from core import clear_plan
from core.filename_pattern import field_text
from redactor_common.cli import EXIT_OK, EXIT_PARTIAL, CliError, Output, add_common_options

from videocli.files import add_path_arguments, collect, load_videos, skip_reason


def add_clear_parser(sub) -> None:
    parser = sub.add_parser(
        "clear", help="empty fields, source ids and the poster",
        description="Empty groups of fields on each video and save it in place (the tags only; the video is not "
                    "re-encoded). The groups: descriptive (title, description, genre, cast, season, episode, lyrics...), "
                    "sources (TMDB/IMDb/TVDB/ThePornDB/StashDB ids, source link, match note), poster (the embedded "
                    "cover), type (the content type; never cleared unless named). The scan stamp, technical fields, "
                    "subtitle files and the MP4 media type are never touched. Refuses to run without --yes or --dry-run.",
    )
    add_path_arguments(parser)
    parser.add_argument("--groups", metavar="LIST", default=",".join(clear_plan.DEFAULT_GROUP_KEYS),
                        help="comma-separated groups to clear: descriptive, sources, poster, type "
                             "(default: descriptive,sources,poster)")
    parser.add_argument("-n", "--dry-run", action="store_true", help="show what would be cleared, write nothing")
    parser.add_argument("--yes", action="store_true", help="really clear and save (required unless --dry-run)")
    add_common_options(parser)
    parser.set_defaults(handler=run_clear)


def parse_groups(text: str) -> list[str]:
    keys: list[str] = []
    for word in text.split(","):
        word = word.strip().lower().replace("-", "_")
        if not word:
            continue
        key = clear_plan.GROUP_ALIASES.get(word)
        if key is None:
            raise CliError(f"unknown group {word!r}: use {', '.join(clear_plan.ALL_GROUP_KEYS)}")
        if key not in keys:
            keys.append(key)
    if not keys:
        raise CliError("no group given: use --groups with one of " + ", ".join(clear_plan.ALL_GROUP_KEYS))
    return keys


def run_clear(args: argparse.Namespace, out: Output) -> int:
    keys = parse_groups(args.groups)
    if not args.yes and not args.dry_run:
        raise CliError("refusing to clear without --yes (use --dry-run to see what would be cleared)")
    files = collect(args.paths, out, recurse=not args.no_recurse)
    failed = 0
    for index, video in enumerate(load_videos(files, out), start=1):
        path = str(video.path)
        out.progress(index, len(files), path)
        row = {"path": path, "status": "", "cleared": {}, "message": ""}
        reason = skip_reason(video)
        if reason:
            row["status"], row["message"] = "failed", reason
            failed += 1
        else:
            plan = clear_plan.plan_for([video]).selected(keys)
            row["cleared"] = dict(plan[0].fields) if plan else {}
            if not row["cleared"]:
                row["status"] = "unchanged"
            elif args.dry_run:
                row["status"] = "planned"
            else:
                clear_plan.stage_clear(video, keys)
                video.save()
                if video.save_error:
                    row["status"], row["message"] = "failed", video.save_error
                    failed += 1
                else:
                    row["status"] = "changed"
        out.record(row)
        out.line(f"{row['status']:9} {path}" + (f"  ({row['message']})" if row["message"] else ""))
        for name, value in row["cleared"].items():
            out.line(f"          {name}: {value[:60]!r}")
    out.finish({"files": len(files), "failed": failed, "dry_run": args.dry_run, "groups": keys})
    return EXIT_PARTIAL if failed else EXIT_OK
