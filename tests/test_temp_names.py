"""
The app's own leftover temp/backup files (interrupted repair, remux/convert,
Redact) must not be listed as videos by a folder scan, while a user's
legitimately named lookalikes still are. Pure filesystem, no external tools.
"""

import pytest

from core.temp_names import is_app_temp_name
from core.video_file import discover_video_files

TEMP_NAMES = [
    "Movie.repairing.mp4",
    "Movie.repairing.mkv",
    "Movie.partial.mp4",
    "Movie.partial.m4v",
    "Movie.redact-orig.mkv",
    "Movie.redact-orig2.mp4",
    "Movie.redact-orig13.mp4",
    ".Movie.redact-1a2b3c4d.mp4",
    ".Movie.redact-1a2b3c4d.partial.mkv",
    "Show.S01E01.partial.mp4",
    "MOVIE.REPAIRING.MP4",
    "Movie.Redact-ORIG.mkv",
    ".Movie.REDACT-ABCDEF01.MKV",
]

CASE_VARIANTS = {"MOVIE.REPAIRING.MP4", "Movie.Redact-ORIG.mkv", ".Movie.REDACT-ABCDEF01.MKV"}

LEGIT_NAMES = [
    "My.Partial.Cut.mkv",
    "Partial.mkv",
    "Repairing The Roof.mp4",
    "Movie.repairing.part2.mp4",
    "The.Partial.Truth.2020.mp4",
    "redact-orig.mp4",
    "Movie.redact-original.mp4",
    "Movie.redact-orig-cut.mkv",
    ".hidden-but-legit.mp4",
    ".Movie.redact-zzzzzzzz.mp4",   # not 8 hex digits
    ".Movie.redact-1a2b3c.mp4",     # too short
    "Movie.mp4",
]


@pytest.mark.parametrize("name", TEMP_NAMES)
def test_app_temp_names_recognised(name):
    assert is_app_temp_name(name)


@pytest.mark.parametrize("name", LEGIT_NAMES)
def test_lookalike_user_names_not_recognised(name):
    assert not is_app_temp_name(name)


def _touch(root, rel):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    return p


def test_discover_skips_every_temp_pattern_and_keeps_lookalikes(tmp_path):
    # The upper-case variants would collide with their lower-case twins on a
    # case-insensitive filesystem (Windows/macOS); they are covered above.
    temp = [n for n in TEMP_NAMES if n not in CASE_VARIANTS]
    for name in temp + LEGIT_NAMES:
        _touch(tmp_path, name)
    ignored = []
    found = discover_video_files(tmp_path, ignored=ignored)
    assert {p.name for p in found} == set(LEGIT_NAMES)
    assert {p.name for p in ignored} == set(temp)


def test_nested_folders_filtered_only_when_recursive(tmp_path):
    _touch(tmp_path, "a/b/Real.mkv")
    _touch(tmp_path, "a/b/Real.repairing.mkv")
    _touch(tmp_path, "a/.Real.redact-00ff00ff.mp4")
    _touch(tmp_path, "a/Real.redact-orig2.mkv")
    ignored = []
    found = discover_video_files(tmp_path, recursive=True, ignored=ignored)
    assert [p.name for p in found] == ["Real.mkv"]
    assert len(ignored) == 3
    # Non-recursive never looks inside, so nothing is skipped either.
    ignored = []
    assert discover_video_files(tmp_path, recursive=False, ignored=ignored) == []
    assert ignored == []


def test_ignored_argument_is_optional(tmp_path):
    _touch(tmp_path, "x.partial.mp4")
    _touch(tmp_path, "x.mp4")
    assert [p.name for p in discover_video_files(tmp_path)] == ["x.mp4"]


def test_non_video_temp_lookalike_is_not_counted_as_ignored(tmp_path):
    _touch(tmp_path, "notes.partial.txt")
    ignored = []
    assert discover_video_files(tmp_path, ignored=ignored) == []
    assert ignored == []


def test_names_match_what_the_app_writes():
    from core.ffmpeg_backend import partial_path
    from core.file_check import repair_path
    import os
    assert is_app_temp_name(os.path.basename(partial_path(os.path.join("d", "a.mp4"))))
    assert is_app_temp_name(os.path.basename(repair_path(os.path.join("d", "a.mkv"))))
