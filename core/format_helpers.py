"""
Display formatters for read-only technical table columns (Duration,
Size). Pure functions -- no Qt, no file I/O -- so they're testable
directly, unlike the table cells that actually call them.
"""

from __future__ import annotations
from typing import Optional


def format_duration(seconds: Optional[float]) -> str:
    """5425.3 -> '1:30:25'. Omits the hours component entirely when
    under an hour ('5:03', not '0:05:03') -- matches how duration is
    conventionally displayed in media players and file managers.
    Returns '' for None/negative input rather than '0:00' or raising,
    since "no duration known" and "zero-length video" are genuinely
    different things worth NOT conflating on screen.
    """
    if seconds is None or seconds < 0:
        return ""
    total_seconds = int(seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_file_size(size_bytes: Optional[int]) -> str:
    """1258291 -> '1.2 MB'. Uses binary (1024-based) units, matching
    what Windows Explorer's file size column shows -- consistent with
    what the user already sees for this same file outside the app.
    Returns '' for None (file vanished / stat failed) rather than '0 B'
    or raising.
    """
    if size_bytes is None or size_bytes < 0:
        return ""
    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"  # unreachable given the loop above, kept for clarity/safety
