"""
core/sidecars.py

The files that belong to a video by name and should travel with it when
it is renamed or moved: an EXISTING poster file (`<stem>-poster.jpg`; this app
no longer creates one, posters are embedded in the video now, but users have
them: Media > Embed Existing Poster Files moves them into the video) and
subtitles (`<stem>.srt` or `<stem>.<lang>.srt`, VideoFile.save_subtitle_sidecar).
Pure file-system logic, no Qt.

Matching is exact on purpose: what follows the video's stem must be
one of those shapes, so "Show S01E01.mkv" never claims the files of
"Show S01E010.mkv". A subtitle is `<stem>.srt` or `<stem>.<language>[.<flag>...].srt`: the language must be a real
one (core/subtitle_ingest's table: en, eng, English, pt-BR...; flags alone are fine) and the flags are the ingest's own (forced, sdh, default,
hi, cc...), so "Movie.Extended.srt" -- the subtitle of Movie.Extended.mkv -- is never taken by Movie.mkv. And when
another video's stem equals the subtitle's name up to a dot ("Movie.en" for Movie.en.srt), the subtitle is that
video's, never this one's (2026-10-16 review).
"""

from __future__ import annotations

import os
import re

from redactor_common.core.move_plan import PlannedMove

from core.ffmpeg_backend import IMPORTABLE_EXTENSIONS
from core.subtitle_ingest import _IGNORED_TAGS, _SDH_TAGS, language_code

_POSTER_SUFFIX_RE = re.compile(r"-poster\.(?:jpe?g|png)", re.IGNORECASE)
_VIDEO_EXTENSIONS = frozenset({".mp4", ".m4v", ".mkv"}) | IMPORTABLE_EXTENSIONS
_SUBTITLE_FLAGS = _SDH_TAGS | (_IGNORED_TAGS - {"srt"})


def _subtitle_middle_ok(middle: str) -> bool:
    """`middle` of <stem>.<middle>.srt is a language code/name, then only flags (forced, sdh, default...)."""
    tokens = middle.split(".")
    rest = tokens[1:] if language_code(tokens[0]) else tokens  # "forced.srt" has no language: flags alone are fine
    return all(t.lower() in _SUBTITLE_FLAGS for t in rest)


def _claimed_by_another_video(stem: str, middle: str, own_name: str, names: list[str]) -> bool:
    """Another video of the folder has the stem `<stem>.<first k tokens of the middle>`: that video owns the subtitle."""
    tokens = middle.split(".")
    candidates = {(stem + "." + ".".join(tokens[:k])).lower() for k in range(1, len(tokens) + 1)}
    for other in names:
        base, ext = os.path.splitext(other)
        if ext.lower() in _VIDEO_EXTENSIONS and other != own_name and base.lower() in candidates:
            return True
    return False


def _is_sidecar(rest: str, stem: str, own_name: str, names: list[str]) -> bool:
    """`rest` = what follows the video's stem in some file name of the folder."""
    if _POSTER_SUFFIX_RE.fullmatch(rest) or rest.lower() == ".srt":
        return True
    if not (rest.startswith(".") and rest.lower().endswith(".srt")):
        return False
    middle = rest[1:-4]
    return _subtitle_middle_ok(middle) and not _claimed_by_another_video(stem, middle, own_name, names)


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
        if other.startswith(stem) and other != name and _is_sidecar(other[len(stem):], stem, name, names):
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


_POSTER_RE = _POSTER_SUFFIX_RE


def poster_sidecars(video_path: str) -> list[str]:
    """The existing poster files of a video (`<stem>-poster.jpg` / .jpeg / .png, exact stem), full paths, .jpg first."""
    folder, name = os.path.split(os.fspath(video_path))
    stem = os.path.splitext(name)[0]
    found = [s for s in sidecar_suffixes(video_path) if _POSTER_RE.fullmatch(s)]
    found.sort(key=lambda suffix: (not suffix.lower().endswith(".jpg"), suffix.lower()))
    return [os.path.join(folder, stem + suffix) for suffix in found]
