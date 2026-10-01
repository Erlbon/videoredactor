"""
Housekeeping for the on-disk thumbnail cache (core.video_file
THUMBNAIL_CACHE_DIR): pruning, atomic writes, and a validity check.

The cache is the app's own subfolder of the temp dir. Pruning only ever
touches regular files there whose names match the app's thumbnail naming
(`<40 hex>.jpg`, plus its in-progress `<40 hex>.<8 hex>.partial.jpg`
temp copies) and refuses to run on a directory that is not the app's
own cache folder, so a mis-set path can never delete someone else's files.
"""

from __future__ import annotations

import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

from redactor_common.core.os_utils import replace_with_retry

CACHE_DIR_NAME = "videoredactor_thumbnails"

MAX_AGE_DAYS = 14
MAX_TOTAL_BYTES = 200 * 1024 * 1024
MAX_FILES = 5000
# Pruning runs inside the first thumbnail request of a session (already
# off the GUI thread); the budget keeps even a huge cache from stalling it.
PRUNE_TIME_BUDGET_SECONDS = 2.0

# `<sha1 hex>.jpg` is a finished thumbnail; the optional middle part is an
# in-progress copy written by write_thumbnail_atomically.
_CACHE_FILE_RE = re.compile(r"^[0-9a-f]{40}(?:\.[0-9a-f]{8}\.partial)?\.jpg$")
_PARTIAL_FILE_RE = re.compile(r"^[0-9a-f]{40}\.[0-9a-f]{8}\.partial\.jpg$")
# A stale in-progress copy (its ffmpeg run died with the app) goes quickly.
_PARTIAL_MAX_AGE_SECONDS = 3600

_JPEG_MAGIC = b"\xff\xd8"

_prune_lock = threading.Lock()
_pruned_dirs: set[str] = set()


def is_valid_thumbnail(path: Path) -> bool:
    """True if `path` is a non-empty file that starts like a JPEG. Catches
    zero-byte and truncated-to-nothing leftovers of a failed run; it does
    not decode the image."""
    try:
        with open(path, "rb") as handle:
            return handle.read(2) == _JPEG_MAGIC
    except OSError:
        return False


def prune_thumbnail_cache(
    cache_dir: Path,
    *,
    max_age_days: float = MAX_AGE_DAYS,
    max_bytes: int = MAX_TOTAL_BYTES,
    max_files: int = MAX_FILES,
    now: Optional[float] = None,
    clock: Callable[[], float] = time.monotonic,
    time_budget: Optional[float] = None,
) -> int:
    """Deletes cache files older than `max_age_days`, then the oldest ones
    until at most `max_files` files and `max_bytes` bytes remain. `now` is
    the wall-clock time to measure age against (default: time.time()).
    Stops early once `time_budget` seconds (measured with `clock`) have
    passed. Returns how many files were deleted; never raises."""
    cache_dir = Path(cache_dir)
    if cache_dir.name != CACHE_DIR_NAME or cache_dir.is_symlink() or not cache_dir.is_dir():
        return 0
    now = time.time() if now is None else now
    started = clock()

    entries = []  # (mtime, size, path)
    try:
        with os.scandir(cache_dir) as it:
            for entry in it:
                if not _CACHE_FILE_RE.match(entry.name):
                    continue
                try:
                    if entry.is_symlink() or not entry.is_file():
                        continue
                    st = entry.stat()
                except OSError:
                    continue
                entries.append((st.st_mtime, st.st_size, Path(entry.path)))
    except OSError:
        return 0

    entries.sort(key=lambda e: e[0])  # oldest first
    cutoff = now - max_age_days * 86400
    partial_cutoff = now - _PARTIAL_MAX_AGE_SECONDS
    total_bytes = sum(e[1] for e in entries)
    remaining = len(entries)
    removed = 0
    for mtime, size, path in entries:
        if time_budget is not None and clock() - started > time_budget:
            break
        too_old = mtime < cutoff or (_PARTIAL_FILE_RE.match(path.name) and mtime < partial_cutoff)
        over_cap = remaining > max_files or total_bytes > max_bytes
        if not (too_old or over_cap):
            continue  # a stale partial copy further on may still qualify
        try:
            path.unlink()
        except OSError:
            continue
        removed += 1
        remaining -= 1
        total_bytes -= size
    return removed


def prune_once_per_session(cache_dir: Path) -> None:
    """Prunes `cache_dir` the first time it is used in this process, with
    the time budget; later calls are free."""
    key = os.path.normcase(os.path.abspath(str(cache_dir)))
    with _prune_lock:
        if key in _pruned_dirs:
            return
        _pruned_dirs.add(key)
    prune_thumbnail_cache(cache_dir, time_budget=PRUNE_TIME_BUDGET_SECONDS)


def partial_thumbnail_path(final_path: Path) -> Path:
    """A unique in-progress name beside `final_path` ("<key>.jpg" ->
    "<key>.<rand>.partial.jpg"; the extension stays .jpg so ffmpeg still
    picks the JPEG muxer)."""
    return final_path.with_name(f"{final_path.stem}.{uuid.uuid4().hex[:8]}.partial.jpg")


def write_thumbnail_atomically(
    final_path: Path, produce: Callable[[str], bool]
) -> bool:
    """Runs `produce(temp_path)` (e.g. ffmpeg extraction) into an
    in-progress name, and only if it reports success AND left a valid JPEG
    moves it over `final_path` in one step. A failed or empty result is
    deleted, so nothing partial is ever read back as a thumbnail."""
    temp = partial_thumbnail_path(final_path)
    try:
        if produce(str(temp)) and is_valid_thumbnail(temp):
            replace_with_retry(str(temp), str(final_path))
            return True
        return False
    except OSError:
        return False
    finally:
        try:
            temp.unlink()
        except OSError:
            pass
