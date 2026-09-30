"""
ffmpeg backend: thumbnail extraction (video "cover" preview), remux, and
transcode.

Unlike mp4_backend/mkv_backend, this module shells out (same "always use
the real CLI tool" precedent as the epub tool's Calibre integration and
this project's own mkv_backend). ffmpeg IS available in this sandbox, so
unlike those other two backends, this one has been run against a real
file, not just syntax-checked -- see tests/test_ffmpeg_backend.py.
"""

from __future__ import annotations
import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Optional

from core.external_tools import get_executable_path
from redactor_common.core.os_utils import rename_no_clobber
from redactor_common.core.subprocess_utils import popen_tool, run_tool

# Extensions offered in the File menu's "Import & Convert to MP4..."
# file picker -- anything ffmpeg's own demuxers commonly handle for a
# "bring this into my video library" workflow. Not exhaustive (ffmpeg
# reads far more than this), just the formats someone converting video
# *to* MP4 would realistically have lying around: old camcorder/phone
# footage (AVI, 3GP, MTS/M2TS), QuickTime/iPhone exports (MOV), old
# Windows tools (WMV), web downloads (FLV, WebM, OGV), and DVD rips
# (MPG/MPEG, VOB). Mirrors mp3redactor's IMPORTABLE_EXTENSIONS
# (core/mp3_converter.py) -- same idea, same shape, different domain.
IMPORTABLE_EXTENSIONS: frozenset[str] = frozenset({
    ".avi", ".mov", ".wmv", ".flv", ".webm", ".mpg", ".mpeg",
    ".m2ts", ".mts", ".ts", ".3gp", ".ogv", ".vob",
})


# Timeouts: a probe or a single-frame grab that hasn't finished in a
# minute is hung (a stalled network share, a pathological file), not
# slow. A remux is a stream copy -- minutes for a very large file at
# worst -- so it gets a much longer ceiling rather than none at all.
PROBE_TIMEOUT_SECONDS = 60
THUMBNAIL_TIMEOUT_SECONDS = 60
REMUX_TIMEOUT_SECONDS = 3 * 60 * 60


def _run(args: list[str], timeout: float = PROBE_TIMEOUT_SECONDS) -> Optional[subprocess.CompletedProcess]:
    """Runs a tool via redactor_common's run_tool() (no console window,
    stdin=DEVNULL so ffmpeg can never sit waiting on an inherited stdin,
    UTF-8 output decoding so a non-ASCII title isn't mangled into cp1252
    mojibake, and a timeout). args[0] is always the LOGICAL executable
    name ("ffmpeg", "ffprobe") as written at each call site -- resolved
    here through get_executable_path(), which substitutes a
    user-configured override, a bundled tools/ copy, or an install-folder
    copy, or leaves the bare name for PATH resolution.

    Returns None if the executable isn't found or the call times out,
    rather than raising -- every caller already has a defined "this
    failed" return shape (None / False / (False, message)).
    """
    resolved_args = [get_executable_path(args[0])] + args[1:]
    try:
        return run_tool(resolved_args, timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired):
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
        timeout=THUMBNAIL_TIMEOUT_SECONDS,
    )
    if result is None:
        return False
    return result.returncode == 0 and Path(output_path).exists()


def partial_path(output_path: str) -> str:
    """The temporary name ffmpeg writes to before the finished file takes
    `output_path`'s name ("a.mp4" -> "a.partial.mp4"; the real extension
    stays last so ffmpeg still picks the MP4 muxer). A crash, failure or
    cancel then never leaves a half-written file under the final name,
    where it would be mistaken for a finished one ("already exists")."""
    p = Path(output_path)
    return str(p.with_name(f"{p.stem}.partial{p.suffix}"))


def _remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _run_ffmpeg(
    args: list[str],
    cancel_event: Optional[threading.Event] = None,
    timeout: Optional[float] = None,
) -> tuple[Optional[int], str]:
    """Runs ffmpeg (args[0] is the logical name) via Popen + polling
    communicate(), so `cancel_event` can kill it mid-run -- `_run()`'s
    plain subprocess.run() blocks until ffmpeg exits. Returns
    (returncode, stderr); returncode None = no result, stderr then says
    why ("Cancelled", a hit timeout, or ffmpeg missing).

    communicate(timeout=...) -- not a bare proc.wait() poll loop -- is
    the documented-safe way to poll a Popen with pipes: ffmpeg writes a
    steady stream of progress info to stderr, and a poll loop that never
    drains the pipes would deadlock once the OS pipe buffer fills.
    Repeated calls after a TimeoutExpired keep draining.
    """
    resolved_args = [get_executable_path(args[0])] + args[1:]
    try:
        proc = popen_tool(
            resolved_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
    except FileNotFoundError:
        return None, "ffmpeg not found on PATH -- is it installed?"

    waited = 0.0
    stderr = ""
    while True:
        try:
            _, stderr = proc.communicate(timeout=0.5)
            break
        except subprocess.TimeoutExpired:
            waited += 0.5
            if cancel_event is not None and cancel_event.is_set():
                proc.kill()
                proc.communicate()
                return None, "Cancelled"
            if timeout is not None and waited >= timeout:
                proc.kill()
                proc.communicate()
                return None, "ffmpeg stopped responding and was stopped."
    return proc.returncode, stderr or ""


def _publish(temp: str, output_path: str) -> tuple[bool, str]:
    """Moves the finished temp file to its final name without replacing
    anything that appeared there meanwhile; (False, reason) otherwise."""
    if not os.path.exists(temp):
        return False, "ffmpeg finished but wrote no output file."
    try:
        rename_no_clobber(temp, output_path)
    except OSError as exc:
        return False, f"couldn't move the result to {os.path.basename(output_path)}: {exc}"
    return True, ""


# What a remux tries, in order: everything (audio/subtitle tracks,
# attachments...); then without attachments and data streams, which MP4
# can't hold; then only the streams MP4 can hold for sure (the last
# resort -- drops subtitle tracks; verify_remux() reports what was lost,
# and the caller must then keep the original).
_REMUX_ATTEMPTS = [
    ["-map", "0"],
    ["-map", "0", "-map", "-0:t", "-map", "-0:d"],
    ["-map", "0:V", "-map", "0:a?"],
]


def remux_to_mp4(
    input_path: str,
    output_path: str,
    cancel_event: Optional[threading.Event] = None,
) -> tuple[bool, str]:
    """Remux (repackage streams, no re-encode) into an MP4 container.

    Fast/lossless since -c copy avoids touching codec data. Every stream
    is mapped (`-map 0`; a bare -c copy keeps only one video and one
    audio track), falling back to fewer streams when MP4 refuses some
    (see _REMUX_ATTEMPTS) -- call verify_remux() afterwards to learn what
    didn't make it. Writes to a temporary name and renames on success, so
    a failed or cancelled remux leaves nothing behind.

    Returns (success, stderr_message) rather than raising -- ffmpeg's
    stderr is the actual useful diagnostic (e.g. "codec not supported in
    MP4") and the caller needs it verbatim to show the user, not a
    generic exception message. (False, "Cancelled") if `cancel_event`
    fired.
    """
    temp = partial_path(output_path)
    message = ""
    try:
        for maps in _REMUX_ATTEMPTS:
            _remove_quietly(temp)
            code, stderr = _run_ffmpeg(
                ["ffmpeg", "-y", "-i", input_path, *maps, "-c", "copy", temp],
                cancel_event=cancel_event, timeout=REMUX_TIMEOUT_SECONDS,
            )
            if code is None:
                return False, stderr
            if code == 0:
                return _publish(temp, output_path)
            message = stderr
        return False, message
    finally:
        _remove_quietly(temp)  # no-op after a successful rename


def _stream_summary(path: str) -> Optional[tuple[dict[str, int], Optional[float]]]:
    """({codec_type: count}, duration seconds or None) via ffprobe; None
    when the file can't be probed."""
    result = _run([
        "ffprobe", "-v", "error", "-show_entries", "stream=codec_type:format=duration",
        "-of", "json", path,
    ])
    if result is None or result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    counts: dict[str, int] = {}
    for stream in data.get("streams") or []:
        kind = stream.get("codec_type") if isinstance(stream, dict) else None
        if kind:
            counts[kind] = counts.get(kind, 0) + 1
    try:
        duration = float((data.get("format") or {}).get("duration"))
    except (ValueError, TypeError):
        duration = None
    return counts, duration


_VERIFIED_KINDS = (("video", "video track"), ("audio", "audio track"),
                   ("subtitle", "subtitle track"), ("attachment", "attachment"))


def verify_remux(source: str, output: str) -> str:
    """Checks `output` (a remux of `source`) kept every video/audio/
    subtitle track and attachment and isn't shorter. Returns "" when
    it did, otherwise what went missing -- the caller must not offer to
    delete the original then."""
    before, after = _stream_summary(source), _stream_summary(output)
    if before is None or after is None:
        return "couldn't compare the result with the original (ffprobe unavailable or failed)"
    problems = []
    for kind, label in _VERIFIED_KINDS:
        lost = before[0].get(kind, 0) - after[0].get(kind, 0)
        if lost > 0:
            problems.append(f"{lost} {label}(s) missing")
    if before[1]:
        if after[1] is None:
            problems.append("no duration")
        elif after[1] < before[1] - max(2.0, before[1] * 0.01):
            problems.append(f"shorter ({after[1]:.0f}s vs {before[1]:.0f}s)")
    return ", ".join(problems)


def transcode_to_mp4(
    input_path: str,
    output_path: str,
    *,
    crf: int = 23,
    audio_bitrate: str = "128k",
    threads: Optional[int] = None,
    cancel_event: Optional[threading.Event] = None,
) -> tuple[bool, str]:
    """Re-encode (not just remux) into H.264/AAC MP4 -- for a source
    remux_to_mp4 can't just stream-copy (a codec MP4 can't hold at all),
    or simply because the user wants a smaller/normalized file. Same
    ffmpeg invocation shape as this project's user has been running by
    hand: `-c:v libx264 -crf <crf> -c:a aac -b:a <audio_bitrate>`, plus
    `-threads <n>` when a thread count is given (omitted entirely
    otherwise, so ffmpeg picks its own default rather than being told
    "0 threads" or some other placeholder value).

    A real encode takes real time -- this is meant to be driven from a
    background thread, with `cancel_event` set from a Cancel button so a
    multi-minute encode can actually be killed instead of just abandoned
    to keep running invisibly (see _run_ffmpeg). Like remux_to_mp4 it
    writes to a temporary name and renames on success, so a failed or
    cancelled encode leaves no partial file behind.

    Returns (True, stderr) on success, (False, stderr) on failure, and
    (False, "Cancelled") if `cancel_event` fired mid-encode -- the
    caller's batch summary can treat a cancellation as neither a normal
    success nor a real per-file failure.
    """
    temp = partial_path(output_path)
    args = [
        "ffmpeg", "-y", "-i", input_path,
        "-c:v", "libx264", "-crf", str(crf),
        "-c:a", "aac", "-b:a", audio_bitrate,
    ]
    if threads:
        args += ["-threads", str(threads)]
    args.append(temp)

    try:
        code, stderr = _run_ffmpeg(args, cancel_event=cancel_event)
        if code is None or code != 0:
            return False, stderr
        ok, problem = _publish(temp, output_path)
        return ok, stderr if ok else problem
    finally:
        _remove_quietly(temp)  # no-op after a successful rename


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
