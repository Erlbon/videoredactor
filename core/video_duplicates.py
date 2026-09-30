"""
core/video_duplicates.py

Operations > Find Duplicates...: groups loaded files that are probably the
same video, for the user to review -- nothing here deletes or changes a file.

Two steps, cheapest first:
1. Duration. Files whose lengths differ by more than DURATION_TOLERANCE
   seconds can't be the same video (a re-encode or remux moves the length
   by milliseconds, a few seconds at most for a different cut/intro), so
   only files with a same-length neighbour are looked at further, and the
   expensive frame decode is skipped for everyone else.
2. A perceptual frame hash (dHash, 64 bits): one frame scaled to 9x8 grey
   pixels, each bit saying whether a pixel is brighter than its right-hand
   neighbour. It survives re-encoding, a different bitrate or resolution
   and mild colour changes; two hashes within HAMMING_THRESHOLD bits
   (default 6 of 64) count as the same picture.

Which frame: the FIRST frame is often black or a studio logo, which would
make unrelated films look alike, so the frame is taken at 10% of the
duration (the same spot the thumbnail uses). If that grab fails (a file
that won't seek, a wrong duration) the first frame is used instead.

A group is a connected set: A~B and B~C put A, B and C together. Files
with no known duration are left out (nothing to pre-group them by).
Pure logic apart from the ffmpeg call -- no Qt.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from core.ffmpeg_backend import THUMBNAIL_TIMEOUT_SECONDS, _run

DURATION_TOLERANCE = 2.0   # seconds
HAMMING_THRESHOLD = 6      # of 64 bits
FRAME_FRACTION = 0.10      # where in the video the frame is taken

_WIDTH, _HEIGHT = 9, 8     # 8 rows x 8 comparisons = 64 bits


@dataclass
class Candidate:
    """One file going into the comparison. `key` is whatever the caller
    wants back (the VideoFile)."""
    key: object
    duration: Optional[float]
    hash: Optional[int] = None


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _grab_gray(path: str, seconds: float) -> Optional[bytes]:
    """The frame at `seconds` as 9x8 grey bytes, each 0-127 (halved so
    every byte is ASCII and survives run_tool's UTF-8 text decoding; only
    the order of neighbouring pixels matters here), or None."""
    done = _run(
        ["ffmpeg", "-v", "error", "-ss", f"{seconds:.3f}", "-i", path, "-frames:v", "1",
         "-vf", f"scale={_WIDTH}:{_HEIGHT}:flags=area,lutyuv=y=val/2",
         "-pix_fmt", "gray", "-f", "rawvideo", "-"],
        timeout=THUMBNAIL_TIMEOUT_SECONDS,
    )
    if done is None or done.returncode != 0 or len(done.stdout) != _WIDTH * _HEIGHT:
        return None
    return bytes(ord(c) for c in done.stdout)


def hash_from_pixels(pixels: bytes) -> int:
    """The 64-bit dHash of 9x8 grey pixels (row-major)."""
    bits = 0
    for row in range(_HEIGHT):
        for col in range(_WIDTH - 1):
            bits = (bits << 1) | (pixels[row * _WIDTH + col] > pixels[row * _WIDTH + col + 1])
    return bits


def frame_hash(path: str, duration: Optional[float]) -> Optional[int]:
    """The dHash of the frame at 10% of the video (the first frame when
    that grab fails), or None if neither can be read."""
    pixels = None
    if duration and duration > 0:
        pixels = _grab_gray(path, duration * FRAME_FRACTION)
    if pixels is None:
        pixels = _grab_gray(path, 0.0)
    return None if pixels is None else hash_from_pixels(pixels)


def _neighbours(items: Sequence[Candidate], tolerance: float):
    """Index pairs (in duration order) whose lengths are within tolerance."""
    order = sorted((i for i, c in enumerate(items) if c.duration is not None), key=lambda i: items[i].duration)
    for pos, i in enumerate(order):
        for j in order[pos + 1:]:
            if items[j].duration - items[i].duration > tolerance:
                break
            yield i, j


def candidates_needing_hash(items: Sequence[Candidate], tolerance: float = DURATION_TOLERANCE) -> list[Candidate]:
    """The files that share a duration window with another file -- the
    only ones worth decoding a frame of."""
    needed: set[int] = set()
    for i, j in _neighbours(items, tolerance):
        needed.update((i, j))
    return [items[i] for i in sorted(needed)]


def group_duplicates(
    items: Sequence[Candidate],
    threshold: int = HAMMING_THRESHOLD,
    tolerance: float = DURATION_TOLERANCE,
) -> list[list[Candidate]]:
    """Groups of 2+ files with near-equal duration and a frame hash within
    `threshold` bits. Files without a hash are ignored. Largest group
    first, each group in input order."""
    parent = list(range(len(items)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, j in _neighbours(items, tolerance):
        a, b = items[i].hash, items[j].hash
        if a is not None and b is not None and hamming(a, b) <= threshold:
            parent[find(i)] = find(j)

    groups: dict[int, list[Candidate]] = {}
    for i, item in enumerate(items):
        if item.hash is not None and item.duration is not None:
            groups.setdefault(find(i), []).append(item)
    found = [g for g in groups.values() if len(g) > 1]
    found.sort(key=lambda g: -len(g))
    return found


def find_duplicates(
    items: Sequence[Candidate],
    hasher: Callable[[Candidate], Optional[int]],
    threshold: int = HAMMING_THRESHOLD,
    tolerance: float = DURATION_TOLERANCE,
) -> list[list[Candidate]]:
    """Convenience for callers without a progress loop (tests): hashes the
    files that need it with `hasher`, then groups."""
    for item in candidates_needing_hash(items, tolerance):
        item.hash = hasher(item)
    return group_duplicates(items, threshold, tolerance)
