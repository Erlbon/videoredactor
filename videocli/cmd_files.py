"""
videocli/cmd_files.py

  rename  PATH...   rename by a tag pattern ("%show_title% - S%season_number%E%episode_number% - %title%")
  move    PATH...   move (or copy) into folders under a library root by a pattern ("%show_title%/Season %season_number%/...")

Rename and move are the shared implementations in redactor_common.cli.commands; this file only says how a
video's fields and path are read. A video's poster and subtitle files (<name>-poster.jpg, <name>.srt,
<name>.<lang>.srt) are renamed and moved with it. A video whose pattern would leave a required field empty is
left as it is, like Redact's Rename step. Renames and moves are recorded in the same log the app's File >
Undo Last Rename reads.
"""

from __future__ import annotations

import argparse

from core.config import get_setting
from core.filename_pattern import DEFAULT_RENAME_PATTERN, placeholder_values
from core.redact_steps import empty_required_tokens
from core.sidecars import sidecar_moves, sidecar_pairs
from core.video_file import VideoFile
from redactor_common.cli import EXIT_OK, EXIT_PARTIAL, Output, add_common_options, commands
from redactor_common.cli.commands import add_pattern_options
from redactor_common.core.rename_pattern import zero_pad_numeric_value

from videocli.files import add_path_arguments, collect, load_videos, rename_log, skip_reason


def _values_for(video: VideoFile, zero_pad: int) -> dict[str, str]:
    values = dict(placeholder_values(video.metadata))
    if zero_pad > 0 and values.get("episode_number"):
        values["episode_number"] = zero_pad_numeric_value(values["episode_number"], zero_pad)
    return values


def _skip_for(pattern: str, zero_pad: int):
    """skip_reason for a pattern: an unreadable file, or a required field the pattern would leave empty."""
    flat = pattern.replace("/", " ").replace("\\", " ")

    def reason(video: VideoFile) -> str:
        base = skip_reason(video)
        if base:
            return base
        missing = empty_required_tokens(flat, _values_for(video, zero_pad))
        return ("empty " + ", ".join(f"%{m}%" for m in missing)) if missing else ""

    return reason


# --- rename -------------------------------------------------------------------------


def add_rename_parser(sub) -> None:
    parser = sub.add_parser(
        "rename", help="rename files by a tag pattern",
        description="Rename each video from its tags, in its own folder; its poster and subtitle files are renamed with "
                    "it. Never overwrites: a name that is taken gets (2), (3)...",
    )
    add_path_arguments(parser)
    add_pattern_options(parser, pattern_required=False)
    add_common_options(parser)
    parser.set_defaults(handler=run_rename)


def run_rename(args: argparse.Namespace, out: Output) -> int:
    pattern = args.pattern or DEFAULT_RENAME_PATTERN
    videos = load_videos(collect(args.paths, out, recurse=not args.no_recurse))
    failed = commands.rename_items(
        videos, pattern=pattern, values_for=lambda v: _values_for(v, args.zero_pad), path_of=lambda v: str(v.path),
        skip_reason=_skip_for(pattern, args.zero_pad), out=out, dry_run=args.dry_run, ascii_only=args.ascii,
        log=rename_log(), log_label="Rename by Pattern (command line)", companions=sidecar_pairs,
    )
    out.finish({"files": len(videos), "failed": failed, "dry_run": args.dry_run, "pattern": pattern})
    return EXIT_PARTIAL if failed else EXIT_OK


# --- move ---------------------------------------------------------------------------


def add_move_parser(sub) -> None:
    parser = sub.add_parser(
        "move", help="move files into folders under a library root",
        description="Move (or copy) each video to <root>/<pattern>, the pattern may contain / to make sub-folders, "
                    "e.g. \"%show_title%/Season %season_number%/%title%\". Its poster and subtitle files travel with it. "
                    "Never overwrites.",
    )
    add_path_arguments(parser)
    add_pattern_options(parser, pattern_required=True)
    parser.add_argument("--root", metavar="FOLDER", help="the library folder (default: the one saved in the app)")
    parser.add_argument("--copy", action="store_true", help="copy instead of move, leaving the originals")
    add_common_options(parser)
    parser.set_defaults(handler=run_move)


def run_move(args: argparse.Namespace, out: Output) -> int:
    root = args.root or get_setting("rename", "library_root", "").strip()
    videos = load_videos(collect(args.paths, out, recurse=not args.no_recurse))
    failed = commands.move_items(
        videos, root=root, pattern=args.pattern, values_for=lambda v: _values_for(v, args.zero_pad),
        path_of=lambda v: str(v.path), skip_reason=_skip_for(args.pattern, args.zero_pad), out=out,
        dry_run=args.dry_run, copy=args.copy, ascii_only=args.ascii, log=rename_log(),
        log_label="Move into folders (command line)", companions=sidecar_moves,
    )
    out.finish({"files": len(videos), "failed": failed, "dry_run": args.dry_run, "root": root})
    return EXIT_PARTIAL if failed else EXIT_OK
