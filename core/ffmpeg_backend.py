"""
ffmpeg backend: thumbnail extraction (video "cover" preview) and remux.

Unlike mp4_backend/mkv_backend, this module shells out (same "always use
the real CLI tool" precedent as the epub tool's Calibre integration and
this project's own mkv_backend). ffmpeg IS available in this sandbox, so
unlike those other two backends, this one has been run against a real
file, not just syntax-checked -- see tests/test_ffmpeg_backend.py.
"""

from __future__ import annotations
import subprocess
import os
from pathlib import Path
from typing import Optional

from core.external_tools import get_executable_path


def _no_console_flags() -> int:
    """CREATE_NO_WINDOW on Windows, matching the epub tool's fix (v35)
    for Calibre subprocess calls popping a console window."""
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0  # type: ignore[attr-defined]


def _run(args: list[str]) -> Optional[subprocess.CompletedProcess]:
    """subprocess.run wrapper shared by every function below. args[0]
    is always the LOGICAL executable name ("ffmpeg", "ffprobe") as
    written at each call site -- resolved here through
    get_executable_path(), which substitutes a user-configured
    settings.ini override when one exists, or leaves it as the bare
    command name for normal PATH resolution otherwise. Every call site
    below stays written against the logical name; only this one
    function needs to know overrides exist at all.

    Returns None if the resolved executable still isn't found
    (FileNotFoundError / WinError2) rather than letting that propagate
    as an uncaught exception -- every caller already has a defined
    "this failed" return shape (None / False / (False, message)), so a
    missing ffmpeg becomes a clean failure through that same shape
    instead of a crash. Reproduced and fixed against this exact
    condition: this development sandbox genuinely lacks MKVToolNix,
    which surfaced the same unguarded-subprocess bug class in
    mkv_backend.py first.
    """
    resolved_args = [get_executable_path(args[0])] + args[1:]
    try:
        return subprocess.run(
            resolved_args, capture_output=True, text=True, creationflags=_no_console_flags(),
        )
    except FileNotFoundError:
        return None


def get_duration_seconds(path: str) -> Optional[float]:
    """Probe a video's duration via ffprobe. Returns None on failure
    rather than raising -- duration is used to pick a sane thumbnail
    timestamp, and a missing/corrupt duration shouldn't block extraction
    entirely (falls back to a fixed early timestamp instead)."""
    result = _run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
    )
    if result is None:
        return None
    try:
        return float(result.stdout.strip())
    except (ValueError, AttributeError):
        return None


def extract_thumbnail(
    video_path: str,
    output_path: str,
    timestamp_seconds: Optional[float] = None,
) -> bool:
    """Extract a single frame as a JPEG thumbnail.

    If timestamp_seconds is omitted, picks 10% into the video (a frame
    early enough to load fast, late enough to usually skip black-frame
    intros/studio logos) -- falls back to 1 second in if duration can't
    be determined at all. Returns True/False rather than raising, since
    the caller (GUI preview panel) wants a clean "couldn't generate a
    thumbnail" case, not an exception to catch on every table selection.
    """
    if timestamp_seconds is None:
        duration = get_duration_seconds(video_path)
        timestamp_seconds = (duration * 0.10) if duration else 1.0

    result = _run(
        [
            "ffmpeg", "-y",
            "-ss", str(timestamp_seconds),
            "-i", video_path,
            "-frames:v", "1",
            "-q:v", "3",  # JPEG quality; 2-5 is "visually lossless enough" range
            output_path,
        ],
    )
    if result is None:
        return False
    return result.returncode == 0 and Path(output_path).exists()


def remux_to_mp4(input_path: str, output_path: str) -> tuple[bool, str]:
    """Remux (repackage streams, no re-encode) into an MP4 container.

    Fast/lossless since -c copy avoids touching codec data. Returns
    (success, stderr_message) rather than raising -- ffmpeg's stderr is
    the actual useful diagnostic (e.g. "codec not supported in MP4") and
    the caller needs it verbatim to show the user, not a generic
    exception message.
    """
    result = _run(["ffmpeg", "-y", "-i", input_path, "-c", "copy", output_path])
    if result is None:
        return False, "ffmpeg not found on PATH -- is it installed?"
    return result.returncode == 0, result.stderr


def probe_technical_info(path: str) -> dict:
    """Extract read-only technical fields (resolution, codecs, bitrate,
    duration, frame rate, container) via ffprobe -- format-agnostic, so
    this is the SAME code path for MP4 and MKV, rather than depending on
    mutagen/mkvmerge separately for this (mutagen in particular doesn't
    reliably expose video stream info like resolution; it's a tagging
    library first, not a general media prober).

    Returns a dict matching VideoMetadata's technical field names,
    ready to apply via setattr in a loop -- an empty dict on failure
    (missing ffmpeg, corrupt file, etc.) rather than raising, since
    missing technical info shouldn't block the rest of a file's load.
    """
    result = _run([
        "ffprobe", "-v", "error",
        "-show_entries", "stream=codec_type,codec_name,width,height,avg_frame_rate",
        "-show_entries", "format=duration,bit_rate,format_name",
        "-of", "json", path,
    ])
    if result is None or result.returncode != 0:
        return {}

    import json as _json
    try:
        data = _json.loads(result.stdout)
    except (ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}  # ffprobe produced valid JSON that wasn't the object shape expected (e.g. bare null/array)

    info: dict = {}
    # dict.get(key, default) only applies `default` when the key is
    # ABSENT -- if ffprobe's JSON has the key present with an explicit
    # null value (a real possibility for a file it can't fully analyze),
    # .get() returns None despite the default, and format_info.get(...)
    # below would then crash with an AttributeError. `or {}`/`or []`
    # catches both "key absent" and "key present but null" uniformly.
    format_info = data.get("format") or {}
    if not isinstance(format_info, dict):
        format_info = {}
    if format_info.get("duration"):
        try:
            info["duration_seconds"] = float(format_info["duration"])
        except (ValueError, TypeError):
            pass
    if format_info.get("bit_rate"):
        info["bitrate"] = format_info["bit_rate"]
    if format_info.get("format_name"):
        # ffprobe reports e.g. "mov,mp4,m4a,3gp,3g2,mj2" for MP4 -- take
        # the first token as a short, readable container label rather
        # than the full comma-heavy string.
        info["container"] = format_info["format_name"].split(",")[0]

    streams = data.get("streams") or []
    if not isinstance(streams, list):
        streams = []
    for stream in streams:
        if not isinstance(stream, dict):
            continue
        codec_type = stream.get("codec_type")
        if codec_type == "video" and "video_codec" not in info:
            info["video_codec"] = stream.get("codec_name", "")
            width, height = stream.get("width"), stream.get("height")
            if width and height:
                info["resolution"] = f"{width}x{height}"
            frame_rate_raw = stream.get("avg_frame_rate", "")
            info["frame_rate"] = _format_frame_rate(frame_rate_raw)
        elif codec_type == "audio" and "audio_codec" not in info:
            info["audio_codec"] = stream.get("codec_name", "")

    return info


def _format_frame_rate(raw: str) -> str:
    """ffprobe reports frame rate as a fraction string like '24000/1001'
    (NTSC-style rates) or '25/1' -- convert to a readable decimal
    ('23.98', '25') rather than exposing the raw fraction to the user.
    Returns '' for anything unparseable (e.g. '0/0' for a stream with
    no meaningful frame rate) rather than raising.
    """
    if not raw or "/" not in raw:
        return raw or ""
    try:
        num, denom = raw.split("/")
        num, denom = float(num), float(denom)
        if denom == 0:
            return ""
        fps = num / denom
        # Whole numbers display without a trailing ".0"; fractional
        # rates (23.976, 29.97) keep 2 decimal places.
        return str(int(fps)) if fps == int(fps) else f"{fps:.2f}"
    except (ValueError, ZeroDivisionError):
        return ""
