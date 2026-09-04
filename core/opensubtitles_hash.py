"""
OpenSubtitles "moviehash" algorithm.

This is the fingerprint used for hash-based subtitle matching: a 64-bit
checksum derived from file size plus the first and last 64KB of the
file's content. Two files with the same hash are (for all practical
purposes) the exact same release/encode, which is what guarantees
subtitle sync -- a title-based search can't make that guarantee, since
different encodes of "the same movie" commonly differ by a few seconds
of intro/outro padding.

Algorithm is OpenSubtitles' own public spec (used by several subtitle
tools/clients), not something invented here. Pure file I/O -- no
network, no API key needed -- so unlike opensubtitles_client.py, this
module IS testable for real in this sandbox.
"""

from __future__ import annotations
import struct
import os
from pathlib import Path
from typing import Optional

CHUNK_SIZE = 65536  # 64KB
MIN_FILE_SIZE = CHUNK_SIZE * 2  # need a full 64KB from both ends


def compute_moviehash(path: str) -> Optional[str]:
    """Return the 16-character lowercase hex moviehash, or None if the
    file is too small (< 128KB) for the algorithm to apply -- returning
    None rather than raising, since the caller (subtitle search) should
    treat "too small to hash" the same as "hash match not attempted,
    fall back to title search" rather than crashing.
    """
    file_size = os.path.getsize(path)
    if file_size < MIN_FILE_SIZE:
        return None

    long_long_format = "<q"  # little-endian signed 64-bit
    chunk_count = CHUNK_SIZE // struct.calcsize(long_long_format)

    hash_value = file_size

    with open(path, "rb") as f:
        for _ in range(chunk_count):
            buf = f.read(8)
            (val,) = struct.unpack(long_long_format, buf)
            hash_value = (hash_value + val) & 0xFFFFFFFFFFFFFFFF

        f.seek(max(0, file_size - CHUNK_SIZE), os.SEEK_SET)
        for _ in range(chunk_count):
            buf = f.read(8)
            (val,) = struct.unpack(long_long_format, buf)
            hash_value = (hash_value + val) & 0xFFFFFFFFFFFFFFFF

    return "%016x" % hash_value
