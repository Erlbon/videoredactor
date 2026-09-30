"""
core/sidecars.py

The files that belong to a video by name and should travel with it when
it is renamed or moved: the poster (`<stem>-poster.jpg`, written by
VideoFile.save_poster_sidecar) and subtitles (`<stem>.srt` or
`<stem>.<lang>.srt`, VideoFile.save_subtitle_sidecar). Pure file-system
logic, no Qt.

Matching is exact on purpose: what follows the video's stem must be
one of those shapes, so "Show S01E01.mkv" never claims the files of
"Show S01E010.mkv".
"""

from __future__ import annotations

import os
import re

from redactor_common.core.move_plan import PlannedMove

_SUFFIX_RE = re.compile(r"(?:-poster\.(?:jpe?g|png)|(?:\.[A-Za-z-]{2,8})?\.srt)", re.IGNORECASE)


def sidecar_suffixes(video_path: str) -> list[str]:
    """What follows the video's stem in each sidecar that exists beside
    it ("-poster.jpg", ".en.srt"...), sorted."""
    folder, name = os.path.split(os.fspath(video_path))
    stem = os.path.splitext(name)[0]
    try:
        names = os.listdir(folder or ".")
    except OSError:
        return []
    found = []
    for other in names:
        if other.startswith(stem) and _SUFFIX_RE.fullmatch(other[len(stem):]) and other != name:
            if os.path.isfile(os.path.join(folder, other)):
                found.append(other[len(stem):])
    return sorted(found)


def sidecar_pairs(old_video: str, new_video: str) -> list[tuple[str, str]]:
    """(old path, new path) for each sidecar of `old_video` when the
    video becomes `new_video` (the new stem replaces the old one)."""
    old_folder, old_name = os.path.split(os.fspath(old_video))
    new_folder, new_name = os.path.split(os.fspath(new_video))
    old_stem, new_stem = os.path.splitext(old_name)[0], os.path.splitext(new_name)[0]
    return [
        (os.path.join(old_folder, old_stem + suffix), os.path.join(new_folder, new_stem + suffix))
        for suffix in sidecar_suffixes(old_video)
    ]


def sidecar_moves(move: PlannedMove) -> list[PlannedMove]:
    """PlannedMoves for the sidecars of a planned video move, so they
    travel with it (same folders to create, same root). Empty for a
    blocked or no-op move."""
    if move.blocking or move.is_noop:
        return []
    return [
        PlannedMove(None, old, new, list(move.dirs_to_create), "", False, move.root)
        for old, new in sidecar_pairs(move.old_path, move.new_path)
    ]


def with_sidecars(planned: list[PlannedMove]) -> list[PlannedMove]:
    """`planned` with each video's sidecar moves right after it (so a
    cancelled run never leaves a moved video without its subtitles)."""
    out: list[PlannedMove] = []
    for move in planned:
        out.append(move)
        out.extend(sidecar_moves(move))
    return out
