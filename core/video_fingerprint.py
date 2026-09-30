"""
core/video_fingerprint.py

A short "has the video itself changed?" fingerprint for scan stamps (see
VideoFile.record_check). Not a hash of the file's bytes: a tag write
(mkvpropedit, an MP4 atom) or a lossless remux shifts payload bytes
without changing the video, and must not invalidate a stamp. So it is
built from what makes the streams what they are:

1. ffprobe: the number of streams, and per stream its type, codec and
   the parameters that define it (width/height/pix_fmt for video,
   sample_rate/channels for audio), plus the container duration rounded
   to whole seconds (a remux may move it by a few milliseconds).
2. A hash of the first PACKET_LIMIT packets of the first video stream
   and of the first audio stream (`ffmpeg -c copy -frames:X N -f md5`:
   no decoding, so it costs the same on a 50 MB clip and a 50 GB one).

Both go into one SHA-256; the result is "v1-<16 hex>". Any failure
(ffprobe/ffmpeg missing, unreadable file, a timeout) returns "" -- "no
fingerprint", never an exception. A re-encode or truncation changes the
parameters, the duration or the packets; a tag edit or a copy-remux of
the same streams changes none of them (MP4 and MKV even hash alike, both
keep H.264/AAC packets as stored).
"""

from __future__ import annotations

import hashlib
import json
from typing import Optional

from core.ffmpeg_backend import _run

PACKET_LIMIT = 200
_VERSION = "v1"
_PROBE_TIMEOUT_SECONDS = 60
_HASH_TIMEOUT_SECONDS = 120

# The per-stream parameters that identify the stream; everything else
# ffprobe reports (bit rate, tags, dispositions) can change in a tag edit.
_STREAM_KEYS = {
    "video": ("codec_name", "width", "height", "pix_fmt"),
    "audio": ("codec_name", "sample_rate", "channels"),
}


def _packet_hash(path: str, selector: str, frames_flag: str) -> Optional[str]:
    """MD5 of the first PACKET_LIMIT packets of one stream ("" when the
    file has no such stream); None if ffmpeg failed."""
    done = _run(
        ["ffmpeg", "-v", "error", "-i", path, "-map", f"0:{selector}", "-c", "copy",
         frames_flag, str(PACKET_LIMIT), "-f", "md5", "-"],
        timeout=_HASH_TIMEOUT_SECONDS,
    )
    if done is None or done.returncode != 0:
        return None
    text = (done.stdout or "").strip()
    if not text.startswith("MD5="):
        return None
    return text[4:]


def video_fingerprint(path: str) -> str:
    """The fingerprint of `path`'s streams (see the module docstring), or
    "" when it can't be computed."""
    probe = _run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path],
        timeout=_PROBE_TIMEOUT_SECONDS,
    )
    if probe is None or probe.returncode != 0:
        return ""
    try:
        info = json.loads(probe.stdout or "{}")
    except json.JSONDecodeError:
        return ""
    if not isinstance(info, dict):
        return ""
    streams = [s for s in (info.get("streams") or []) if isinstance(s, dict)]
    if not streams:
        return ""
    try:
        duration = round(float((info.get("format") or {}).get("duration")))
    except (TypeError, ValueError):
        duration = -1  # no recorded duration: still a stable value for the same file

    parts = [f"streams={len(streams)}", f"duration={duration}"]
    for stream in streams:
        kind = stream.get("codec_type") or "?"
        keys = _STREAM_KEYS.get(kind, ("codec_name",))
        parts.append(kind + ":" + ",".join(f"{k}={stream.get(k)}" for k in keys))

    kinds = {s.get("codec_type") for s in streams}
    # Only streams the file has: ffmpeg given an unmatched optional map
    # falls back to its default stream choice, which would hash the
    # video under the audio's name.
    for kind, selector, flag in (("video", "v:0", "-frames:v"), ("audio", "a:0", "-frames:a")):
        if kind not in kinds:
            continue
        packets = _packet_hash(path, selector, flag)
        if packets is None:
            return ""
        parts.append(f"{selector}={packets}")

    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"{_VERSION}-{digest}"
