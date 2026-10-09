"""
videocli/cmd_tags.py

  info PATH...   what each video is: container, resolution, codecs, length, and its tags
  set  PATH...   change tag fields (-s show_title=Dune -s season=1, --clear comment), saved in place
"""

from __future__ import annotations

import argparse

from core.filename_pattern import field_text, set_field_text
from core.video_file import VideoFile
from redactor_common.cli import EXIT_OK, EXIT_PARTIAL, CliError, Output, add_common_options

from videocli.fields import DEFAULT_INFO_FIELDS, FIELD_NAMES, parse_assignment, resolve_field
from videocli.files import add_path_arguments, collect, load_videos, skip_reason


def _duration_text(seconds) -> str:
    if not seconds:
        return ""
    total = int(round(seconds))
    return f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}"


# --- info ---------------------------------------------------------------------------


def add_info_parser(sub) -> None:
    parser = sub.add_parser("info", help="show what the video files are", description="Show each video's technical details and tags.")
    add_path_arguments(parser)
    parser.add_argument("--fields", metavar="LIST", help="comma-separated tag fields to show (default: content_type, title, show_title, season_number, episode_number, release_date)")
    parser.add_argument("--all", action="store_true", help="show every tag field that has a value")
    add_common_options(parser)
    parser.set_defaults(handler=run_info)


def run_info(args: argparse.Namespace, out: Output) -> int:
    files = collect(args.paths, out, recurse=not args.no_recurse)
    wanted = FIELD_NAMES if args.all else (
        [resolve_field(name) for name in args.fields.split(",") if name.strip()] if args.fields else DEFAULT_INFO_FIELDS
    )
    failed = 0
    for index, video in enumerate(load_videos(files, out), start=1):
        path = str(video.path)
        out.progress(index, len(files), path)
        md = video.metadata
        fields = {f: field_text(md, f) for f in wanted}
        fields = {f: v for f, v in fields.items() if v.strip()}
        status = video.load_error or "ok"
        failed += bool(video.load_error)
        technical = {
            "container": md.container, "resolution": md.resolution, "video_codec": md.video_codec,
            "audio_codec": md.audio_codec, "bitrate": md.bitrate, "frame_rate": md.frame_rate,
            "duration_seconds": md.duration_seconds,
        }
        out.record({"path": path, "status": status, "check": video.scan_status(), "technical": technical, "fields": fields})
        audio = f"{md.audio_codec} audio" if md.audio_codec else ""
        fps = f"{md.frame_rate} fps" if md.frame_rate else ""
        details = ", ".join(p for p in (
            md.container, md.resolution, md.video_codec, audio, fps, _duration_text(md.duration_seconds),
            f"check: {video.scan_status()}" if video.scan_status() else "", status,
        ) if p)
        out.line(f"{path}  [{details}]")
        for name, value in fields.items():
            out.line(f"  {name}: {value}")
    out.finish({"files": len(files), "failed": failed})
    return EXIT_PARTIAL if failed else EXIT_OK


# --- set ----------------------------------------------------------------------------


def add_set_parser(sub) -> None:
    parser = sub.add_parser(
        "set", help="change tag fields",
        description="Set or empty tag fields and save each video in place (the tags only; the video is not re-encoded). "
                    "The save is read back and compared.",
    )
    add_path_arguments(parser)
    parser.add_argument("-s", "--set", dest="assignments", action="append", default=[], metavar="FIELD=VALUE",
                        help="set a field (repeat for several), e.g. -s show_title=Dune -s season=1")
    parser.add_argument("--clear", action="append", default=[], metavar="FIELD", help="empty a field (repeatable)")
    parser.add_argument("-n", "--dry-run", action="store_true", help="show the changes, write nothing")
    add_common_options(parser)
    parser.set_defaults(handler=run_set)


def run_set(args: argparse.Namespace, out: Output) -> int:
    changes: dict[str, str] = {}
    for text in args.assignments:
        field, value = parse_assignment(text)
        changes[field] = value
    for name in args.clear:
        changes[resolve_field(name)] = ""
    if not changes:
        raise CliError("nothing to change: give at least one -s FIELD=VALUE or --clear FIELD")

    files = collect(args.paths, out, recurse=not args.no_recurse)
    failed = 0
    for index, video in enumerate(load_videos(files, out), start=1):
        path = str(video.path)
        out.progress(index, len(files), path)
        row = {"path": path, "status": "", "changes": {}, "message": ""}
        reason = skip_reason(video)
        if reason:
            row["status"], row["message"] = "failed", reason
            failed += 1
        else:
            for field, new in changes.items():
                old = field_text(video.metadata, field)
                if old != new:
                    row["changes"][field] = {"old": old, "new": new}
            if not row["changes"]:
                row["status"] = "unchanged"
            elif args.dry_run:
                row["status"] = "planned"
            else:
                row["status"] = _save(video, changes, row)
                failed += row["status"] == "failed"
        out.record(row)
        out.line(f"{row['status']:9} {path}" + (f"  ({row['message']})" if row["message"] else ""))
        for field, change in row["changes"].items():
            out.line(f"          {field}: {change['old']!r} -> {change['new']!r}")
    out.finish({"files": len(files), "failed": failed, "dry_run": args.dry_run})
    return EXIT_PARTIAL if failed else EXIT_OK


def _save(video: VideoFile, changes: dict[str, str], row: dict) -> str:
    refused = [field for field, value in changes.items() if not set_field_text(video.metadata, field, value)]
    if refused:
        row["message"] = "the value was not accepted for " + ", ".join(refused)
        return "failed"
    video.dirty = True
    video.save()
    if video.save_error:
        row["message"] = video.save_error
        return "failed"
    return "changed"
