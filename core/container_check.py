"""
core/container_check.py

Does a video file's container match its extension? A file named .mp4 that is really an FLV (found 2026-10-17:
two clips of a library, "players report file errors, content intact") is invisible to everything that decides by
extension. This module compares what the file REALLY is with what its name says.

What the file really is, in order of trust:
1. ffprobe's `format_name` ("mov,mp4,m4a,3gp,3g2,mj2", "matroska,webm", "flv", "avi", "asf", "mpegts", "mpeg",
   "ogg"...), a comma list of every name the demuxer answers to;
2. the magic bytes at the start of the file (FLV, RIFF/AVI, ASF, MPEG-TS, Ogg, MPEG program stream, ISO base media,
   Matroska) -- the fallback when ffprobe says nothing (not installed, or it fails on a damaged file).

Each real container maps to the extensions it legitimately carries (an MP4-family file may be .mp4 .m4v .mov .m4a
.3gp .3g2 .mj2 ...; Matroska may be .mkv .webm .mka ...), so a legitimate file is never flagged. Only the extensions
in CHECKED_EXTENSIONS are judged (the modern containers players trust by name); a container this table does not
know (wav, mp3, an image...) gives no verdict at all. Pure functions on names and bytes, except
check_container(), which may call ffprobe.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Optional

# Real container key -> (label for messages, extensions it legitimately carries).
CONTAINERS: dict[str, tuple[str, frozenset[str]]] = {
    "mp4": ("MP4/MOV", frozenset({".mp4", ".m4v", ".mov", ".m4a", ".3gp", ".3g2", ".mj2", ".f4v", ".qt", ".m4b"})),
    "matroska": ("Matroska", frozenset({".mkv", ".webm", ".mka", ".mk3d"})),
    "avi": ("AVI", frozenset({".avi", ".divx"})),
    "flv": ("FLV", frozenset({".flv", ".f4v"})),
    "asf": ("ASF", frozenset({".asf", ".wmv", ".wma"})),
    "mpegts": ("MPEG-TS", frozenset({".ts", ".m2ts", ".mts", ".m2t", ".tp", ".trp"})),
    "mpeg": ("MPEG-PS", frozenset({".mpg", ".mpeg", ".mpe", ".vob", ".m2p", ".ps", ".dat"})),
    "ogg": ("Ogg", frozenset({".ogg", ".ogv", ".ogm", ".oga", ".ogx", ".opus"})),
    "rm": ("RealMedia", frozenset({".rm", ".rmvb"})),
}

# ffprobe format_name token -> container key.
_FORMAT_TOKENS: dict[str, str] = {
    "mov": "mp4", "mp4": "mp4", "m4a": "mp4", "3gp": "mp4", "3g2": "mp4", "mj2": "mp4",
    "matroska": "matroska", "webm": "matroska",
    "avi": "avi", "flv": "flv", "asf": "asf", "mpegts": "mpegts", "mpeg": "mpeg", "ogg": "ogg", "rm": "rm",
}

# The extensions that are judged. A file with any other extension is never flagged.
CHECKED_EXTENSIONS = frozenset({".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi"})

SNIFF_BYTES = 4096  # enough for the MPEG-TS sync check at 188-byte spacing


@dataclass(frozen=True)
class ContainerVerdict:
    key: str  # the real container's key in CONTAINERS
    label: str  # "FLV"
    extension: str  # the file's extension, lower case
    wrong: bool  # the extension is not one this container carries
    source: str  # "ffprobe" or "magic"

    @property
    def message(self) -> str:
        return f"wrong container ({self.extension} file is really {self.label})"


def container_key_from_format_name(format_name: str) -> Optional[str]:
    """The container key for ffprobe's comma list, or None when it is not a container this module knows."""
    for token in (format_name or "").lower().split(","):
        key = _FORMAT_TOKENS.get(token.strip())
        if key:
            return key
    return None


def sniff_container(head: bytes) -> Optional[str]:
    """The container key from the first bytes of a file, or None when they say nothing known."""
    if head[:4] == b"FLV\x01":
        return "flv"
    if head[:4] == b"RIFF" and head[8:12] == b"AVI ":
        return "avi"
    if head[:16] == bytes.fromhex("3026b2758e66cf11a6d900aa0062ce6c"):
        return "asf"
    if head[:4] == b"OggS":
        return "ogg"
    if head[:4] == bytes.fromhex("1a45dfa3"):
        return "matroska"
    if head[:4] == b".RMF":
        return "rm"
    if head[:4] == bytes.fromhex("000001ba"):
        return "mpeg"
    if head[4:8] in (b"ftyp", b"moov", b"mdat", b"wide", b"free", b"skip"):
        return "mp4"
    # MPEG-TS: a 0x47 sync byte every 188 bytes (192 in an M2TS, which has a 4-byte timestamp first).
    for offset, step in ((0, 188), (4, 192)):
        if len(head) > offset + 2 * step and all(head[offset + n * step] == 0x47 for n in range(3)):
            return "mpegts"
    return None


def verdict_for(path: str, key: Optional[str], source: str) -> Optional[ContainerVerdict]:
    """The verdict for a real container `key` and the extension of `path` (None: no verdict possible, or the
    extension is not one that is judged)."""
    ext = os.path.splitext(path)[1].lower()
    if key is None or key not in CONTAINERS or ext not in CHECKED_EXTENSIONS:
        return None
    label, extensions = CONTAINERS[key]
    return ContainerVerdict(key=key, label=label, extension=ext, wrong=ext not in extensions, source=source)


def wrong_container(path: str, format_name: str) -> Optional[ContainerVerdict]:
    """The verdict from ffprobe's format_name alone (no file access), or None when the container is right, the
    format_name is empty or not known, or the extension is not judged. Only a WRONG container is returned."""
    verdict = verdict_for(path, container_key_from_format_name(format_name), "ffprobe")
    return verdict if verdict is not None and verdict.wrong else None


def read_head(path: str, count: int = SNIFF_BYTES) -> bytes:
    with open(path, "rb") as handle:
        return handle.read(count)


def check_container(
    path: str, format_name: Optional[str] = None, *, probe_format: Optional[Callable[[str], Optional[str]]] = None,
    read: Callable[[str], bytes] = read_head,
) -> Optional[ContainerVerdict]:
    """The verdict for `path`: from `format_name` when the caller already has it, else from `probe_format(path)`
    (ffprobe's format_name; default: ask ffprobe), else -- ffprobe missing or failing -- from the magic bytes.
    Returns the verdict for a RIGHT container too (wrong=False); None only when nothing is known or the extension
    is not judged. Never raises: an unreadable file has no verdict."""
    if os.path.splitext(path)[1].lower() not in CHECKED_EXTENSIONS:
        return None
    if format_name is None:
        format_name = (probe_format or probe_format_name)(path)
    if format_name:
        key = container_key_from_format_name(format_name)
        if key is not None:
            return verdict_for(path, key, "ffprobe")
    try:
        return verdict_for(path, sniff_container(read(path)), "magic")
    except OSError:
        return None


def probe_format_name(path: str) -> Optional[str]:
    """ffprobe's format_name for `path` (None when ffprobe is missing or fails)."""
    from core.ffmpeg_backend import _run  # noqa: PLC0415

    result = _run(["ffprobe", "-v", "error", "-show_entries", "format=format_name", "-of", "default=nw=1:nk=1", path])
    if result is None or result.returncode != 0:
        return None
    return (result.stdout or "").strip() or None
