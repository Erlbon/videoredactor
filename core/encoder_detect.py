"""
core/encoder_detect.py

Which HEVC encoder the conversion rules' bitrate re-encodes use (ConversionSettings.encoder):

  "nvenc"  always hevc_nvenc (an NVIDIA card)
  "x265"   always libx265, in software (any computer; slower)
  "auto"   (default) hevc_nvenc only when this ffmpeg lists it (`ffmpeg -hide_banner -encoders`) AND a
           one-second test encode with it succeeds; otherwise libx265. Listing alone is not enough: most
           ffmpeg builds list hevc_nvenc whether or not the computer has an NVIDIA card.

The answer is cached for the process, per ffmpeg executable: when the ffmpeg path setting changes the next
conversion checks again. The probes run through ffmpeg_backend._run (no console window, timeout); both are
injectable so tests never need a GPU. Qt-free; safe to call from a worker thread.
"""

from __future__ import annotations

import re
import threading
from typing import Callable, Optional

from core import conversion_plan as cp

LIST_ARGS = ["ffmpeg", "-hide_banner", "-encoders"]
TEST_ARGS = [
    "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=25",
    "-c:v", "hevc_nvenc", "-f", "null", "-",
]
_LISTED = re.compile(r"\bhevc_nvenc\b")

_lock = threading.Lock()
_cache: dict[str, bool] = {}


def _default_run(args: list[str], timeout: float):
    from core.ffmpeg_backend import _run  # noqa: PLC0415 -- keeps this module light to import

    return _run(args, timeout=timeout)


def _default_ffmpeg_path() -> str:
    from core.external_tools import get_executable_path  # noqa: PLC0415

    return get_executable_path("ffmpeg")


def probe_nvenc(run: Callable = _default_run) -> bool:
    """Runs the two checks (no cache): listed, then a one-second test encode that must exit 0."""
    listed = run(LIST_ARGS, timeout=30)
    if listed is None or listed.returncode != 0 or not _LISTED.search(listed.stdout or ""):
        return False
    encoded = run(TEST_ARGS, timeout=60)
    return encoded is not None and encoded.returncode == 0


def nvenc_available(
    *, run: Callable = _default_run, ffmpeg_path: Callable[[], str] = _default_ffmpeg_path,
) -> bool:
    """probe_nvenc() once per ffmpeg executable per process."""
    key = ffmpeg_path() or "ffmpeg"
    with _lock:
        if key in _cache:
            return _cache[key]
    result = probe_nvenc(run)  # outside the lock: it takes a second or two
    with _lock:
        _cache[key] = result
    return result


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def resolve_encoder(
    settings: Optional[cp.ConversionSettings], *, available: Callable[[], bool] = nvenc_available,
) -> str:
    """"nvenc" or "x265" for this run, from settings.encoder ("auto" asks `available`)."""
    choice = (settings or cp.ConversionSettings()).encoder
    if choice == cp.ENCODER_NVENC:
        return cp.ENCODER_NVENC
    if choice == cp.ENCODER_X265:
        return cp.ENCODER_X265
    return cp.ENCODER_NVENC if available() else cp.ENCODER_X265
