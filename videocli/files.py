"""
videocli/files.py

Finding and loading the video files a command works on, and the app's own log, read without any window.
"""

from __future__ import annotations

import os
from pathlib import Path

from core.temp_names import is_app_temp_name
from core.video_file import SUPPORTED_EXTENSIONS, VideoFile
from redactor_common.cli import CliError, Output, expand_paths

EXTENSIONS = tuple(sorted(SUPPORTED_EXTENSIONS))


def add_path_arguments(parser) -> None:
    parser.add_argument("paths", nargs="+", metavar="PATH", help="video files (.mp4, .m4v, .mkv), folders or wildcards")
    parser.add_argument(
        "-R", "--no-recurse", action="store_true", help="for a folder, look only at the files directly in it"
    )


def collect(paths: list[str], out: Output, recurse: bool = True) -> list[str]:
    """The video files the arguments name (the app's own leftover temp files are not listed). An argument that
    matches nothing is an error; if that leaves no files at all the command ends with a usage error."""
    files, missing = expand_paths(paths, EXTENSIONS, recursive=recurse)
    # The app's own leftover temp files are left out of what a folder or wildcard finds; a file named in full is
    # taken as the user meant it.
    named = {os.path.normcase(os.path.abspath(p)) for p in paths if os.path.isfile(p)}
    files = [
        f for f in files
        if os.path.normcase(os.path.abspath(f)) in named or not is_app_temp_name(os.path.basename(f))
    ]
    for argument in missing:
        out.error(f"nothing found for {argument}")
    if not files:
        raise CliError("no video files found")
    return files


def load_videos(files: list[str], out: Output | None = None) -> list[VideoFile]:
    """Loads each file; `out` shows "reading N of M" on stderr while a big batch is read."""
    videos = []
    for index, path in enumerate(files, start=1):
        if out is not None:
            out.progress(index, len(files), f"reading {path}")
        video = VideoFile(path=Path(path))
        video.load()
        videos.append(video)
    return videos


def skip_reason(video: VideoFile) -> str:
    return f"the file could not be read ({video.load_error})" if video.load_error else ""
