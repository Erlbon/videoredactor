"""
core/cut_damaged.py

Cut Out Damaged Parts: the repair for a file that cannot be fetched again. A deep scan (core/deep_scan.py) found
blocks of zero bytes inside the media data; a remux cannot bring those pictures and sounds back, but everything
AROUND them is fine. This cuts the damaged parts out and keeps the rest, losslessly (stream copy, no re-encoding):

1. The good segments are the time between the damaged spans. Every span is a whole number of GOPs (it starts at a
   video keyframe and ends at the next good one), so every stream is cut at the same times and each segment starts
   on a keyframe.
2. Each segment is copied out of the original with ffmpeg (-ss/-to, -c copy) and the segments are joined with the
   concat demuxer (-c copy) into a NEW file next to the original (a hidden temp name, as the repair uses). Video,
   audio and subtitle streams are kept; the app's tags and the embedded poster are put back with the same helpers
   the repair uses (restore_app_metadata).
3. The new file is verified: a full decode (ffmpeg -v error -f null) must print NOTHING, the file's own quick check
   must pass, the audio and video durations must agree (0.3 s) and the length must be the original's minus the cut
   spans (0.5 s, plus 0.1 s per join: ffmpeg's concat rounds each join to a frame or two).
4. Only then does the new file replace the original, exactly like the repair: the original goes to the Recycle
   Bin (the undo), never deleted outright. Any failure, and Cancel, leaves the original untouched and removes
   every scratch file.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from redactor_common.core.trash import move_to_trash

from core.deep_scan import DamagedSpan
from core.ffmpeg_backend import REMUX_TIMEOUT_SECONDS, _run, _run_ffmpeg
from core.file_access import remove_scratch
from core.file_check import (
    CheckResult,
    RepairError,
    _clean,
    _format_seconds,
    quick_check,
    repair_path,
    restore_app_metadata,
    swap_in,
)

# The share of the duration above which the confirmation warns strongly (and defaults to Cancel).
WARN_FRACTION = 0.25
_MIN_SEGMENT_SECONDS = 0.05
_SYNC_TOLERANCE = 0.3
_LENGTH_TOLERANCE = 0.5
_LENGTH_TOLERANCE_PER_JOIN = 0.1
_KEY_MATCH = 0.002


class CutCancelled(RepairError):
    """The user cancelled; the original was not touched."""


@dataclass
class CutPlan:
    spans: list[DamagedSpan]
    duration: float

    @property
    def removed_seconds(self) -> float:
        return sum(s.seconds for s in self.spans)

    @property
    def kept_seconds(self) -> float:
        return max(self.duration - self.removed_seconds, 0.0)

    @property
    def fraction(self) -> float:
        return self.removed_seconds / self.duration if self.duration else 0.0

    @property
    def heavy(self) -> bool:
        """More than a quarter of the file would be lost."""
        return self.fraction > WARN_FRACTION

    def ranges_text(self, limit: int = 12) -> str:
        parts = [f"{_format_seconds(s.start_s)}-{_format_seconds(s.end_s)}" for s in self.spans[:limit]]
        if len(self.spans) > limit:
            parts.append(f"... (+{len(self.spans) - limit} more)")
        return ", ".join(parts)

    def sentence(self) -> str:
        n = len(self.spans)
        return (
            f"This removes {n} part{'s' if n != 1 else ''} totalling {_format_seconds(self.removed_seconds)} "
            f"({self.ranges_text()}) of video and audio from the file; the rest is kept unchanged. "
            f"The original goes to the Recycle Bin."
        )


def plan_cut(check: CheckResult) -> CutPlan:
    """What a cut would remove, from the deep scan in `check`. Raises RepairError when there is nothing to cut."""
    deep = getattr(check, "deep", None)
    if deep is None or not deep.spans:
        raise RepairError("no damaged parts were found by a Deep scan")
    duration = deep.duration or check.declared_seconds or 0.0
    return CutPlan(spans=list(deep.spans), duration=duration)


@dataclass
class CutOutcome:
    check: CheckResult              # the new file's own check
    removed: list[DamagedSpan]
    removed_seconds: float
    new_duration: float
    decode_errors: int = 0
    verify: str = ""                # a sentence for the report
    note: str = ""                  # e.g. tags that couldn't be restored


def _quote(path: str) -> str:
    """A path as an ffconcat 'file' argument: forward slashes, a single quote written as quote-backslash-quote-quote."""
    backslash = chr(92)
    text = path.replace(backslash, "/").replace("'", "'" + backslash + "''")
    return "'" + text + "'"


def _good_segments(plan: CutPlan, deep) -> list[tuple[float, Optional[float]]]:
    """[(start, end)] of the time to keep: `start` is a keyframe's presentation time (where a copy can begin), `end`
    is the DECODE time of the keyframe that opens the next damaged span (packets from there on belong to it), None
    for the last segment (to the end of the file)."""
    dts_by_pts = deep.keyframes

    def decode_time(pts: float) -> float:
        best = None
        for key_pts, key_dts in dts_by_pts:
            if abs(key_pts - pts) <= _KEY_MATCH and (best is None or abs(key_pts - pts) < best[0]):
                best = (abs(key_pts - pts), key_dts)
        return best[1] if best else pts

    segments: list[tuple[float, Optional[float]]] = []
    cursor = 0.0
    for span in plan.spans:
        if span.start_s - cursor > _MIN_SEGMENT_SECONDS:
            segments.append((cursor, decode_time(span.start_s)))
        cursor = max(cursor, span.end_s)
    if plan.duration - cursor > _MIN_SEGMENT_SECONDS or not segments:
        segments.append((cursor, None))
    return segments


def _probe(path: str, run) -> Optional[dict]:
    done = run(["ffprobe", "-v", "error", "-show_entries",
                "format=duration:stream=index,codec_type,start_time,duration:stream_disposition=attached_pic",
                "-of", "json", path])
    if done is None or done.returncode != 0:
        return None
    try:
        return json.loads(done.stdout or "{}")
    except json.JSONDecodeError:
        return None


def _float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _stream_times(info: dict) -> dict[str, tuple[Optional[float], Optional[float]]]:
    """{"video": (start, duration), "audio": (...)} of the first real video and audio streams."""
    found: dict[str, tuple[Optional[float], Optional[float]]] = {}
    for stream in info.get("streams") or []:
        kind = stream.get("codec_type")
        if kind in found or kind not in ("video", "audio"):
            continue
        if (stream.get("disposition") or {}).get("attached_pic"):
            continue
        found[kind] = (_float(stream.get("start_time")), _float(stream.get("duration")))
    return found


def build_cut_copy(
    path: str,
    check: CheckResult,
    target: Optional[str] = None,
    run: Callable = _run,
    ffmpeg: Callable = _run_ffmpeg,
    restore: Callable[[str, str], str] = restore_app_metadata,
    cancel: Optional[threading.Event] = None,
    progress: Optional[Callable[[int, int], None]] = None,
    status: Optional[Callable[[str], None]] = None,
) -> CutOutcome:
    """The first half of cut_out_damaged(): writes the cut copy to `target` (default repair_path(path)), restores the
    app's metadata and verifies it. The original is not touched, nothing is trashed or swapped. Raises RepairError
    (CutCancelled for a cancel; the copy and every scratch file removed) when anything fails."""
    plan = plan_cut(check)
    deep = check.deep
    try:
        stat = os.stat(path)
    except OSError as exc:
        raise RepairError(f"can't read the file: {exc}") from exc
    if (deep.file_size and stat.st_size != deep.file_size) or (deep.mtime_ns and stat.st_mtime_ns != deep.mtime_ns):
        raise RepairError("the file changed since the Deep scan -- run Deep scan again")
    if plan.kept_seconds < 1.0:
        raise RepairError("nothing worth keeping would be left")
    target = target or repair_path(path)
    if os.path.exists(target):
        raise RepairError(f"{os.path.basename(target)} already exists -- remove it first")
    suffix = Path(path).suffix
    stem = target[: -len(suffix)] if suffix and target.endswith(suffix) else target
    segments = _good_segments(plan, deep)
    scratch: list[str] = []
    total_steps = len(segments) + 4
    step = [0]

    def advance(message: str) -> None:
        if cancel is not None and cancel.is_set():
            raise CutCancelled("cancelled")
        if status is not None:
            status(message)
        if progress is not None:
            progress(step[0], total_steps)
        step[0] += 1

    def run_ffmpeg(args: list[str]) -> tuple[Optional[int], str]:
        code, stderr = ffmpeg(args, cancel, REMUX_TIMEOUT_SECONDS)
        if cancel is not None and cancel.is_set():
            raise CutCancelled("cancelled")
        return code, stderr

    try:
        stream_maps = ["-map", "0:V", "-map", "0:a?", "-map", "0:s?", "-map", "0:t?"]
        names: list[str] = []
        for index, (start, end) in enumerate(segments):
            advance(f"Copying part {index + 1} of {len(segments)}")
            name = f"{stem}.part{index}{suffix}"
            scratch.append(name)
            args = ["ffmpeg", "-v", "error", "-n"]
            if start > 0:
                # Seeking is by presentation time: a hair AFTER the keyframe's lands on it (a hair before lands on the
                # keyframe before it -- the damaged one); the keyframe is kept, only flagged "don't show".
                args += ["-ss", f"{start + 0.0001:.6f}"]
            if end is not None:
                args += ["-to", f"{end - 0.0005:.6f}"]
            args += ["-i", path, *stream_maps, "-c", "copy", "-map_metadata", "0", "-avoid_negative_ts", "make_zero", name]
            code, stderr = run_ffmpeg(args)
            if code != 0 and os.path.exists(name):
                remove_scratch(name)
                # A stream type this container can't take (attachments in an MP4...): the real streams only.
                code, stderr = run_ffmpeg(args[:args.index("-map")] + ["-map", "0:V", "-map", "0:a?", "-map", "0:s?"]
                                          + args[args.index("-c"):])
            if code != 0 or not os.path.exists(name):
                raise RepairError(f"couldn't copy part {index + 1}: {_clean((stderr.strip().splitlines() or ['ffmpeg failed'])[-1])}")
            names.append(name)
        advance("Joining the parts")
        listing = f"{target}.txt"
        scratch.append(listing)
        with open(listing, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("ffconcat version 1.0\n")
            for name in names:
                handle.write(f"file {_quote(name)}\n")
        extra = ["-movflags", "+faststart"] if suffix.lower() in (".mp4", ".m4v", ".mov") else []
        code, stderr = run_ffmpeg(["ffmpeg", "-v", "error", "-n", "-f", "concat", "-safe", "0", "-i", listing,
                                   "-map", "0", "-c", "copy", "-map_metadata", "0", *extra, target])
        if code != 0 or not os.path.exists(target):
            raise RepairError(f"couldn't join the parts: {_clean((stderr.strip().splitlines() or ['ffmpeg failed'])[-1])}")
        segment_total = 0.0
        for name in names:
            info = _probe(name, run) or {}
            segment_total += _float((info.get("format") or {}).get("duration")) or 0.0
        for name in names:
            remove_scratch(name)
        scratch.clear()
        remove_scratch(listing)

        advance("Restoring tags and poster")
        try:
            note = restore(path, target)
        except RepairError:
            raise
        except Exception as exc:  # mutagen/OSError/timeout while copying tags: the original stays
            raise RepairError(f"couldn't restore the tags: {exc}") from exc

        advance("Checking the new file (full decode)")
        code, stderr = run_ffmpeg(["ffmpeg", "-v", "error", "-i", target, "-map", "0:V", "-map", "0:a?", "-f", "null", "-"])
        if code is None:
            raise RepairError(f"the check of the new file didn't run: {stderr.strip()[:200]}")
        problems = [line for line in stderr.replace("\r", "\n").splitlines() if line.strip()]
        if code != 0 or problems:
            first = _clean(problems[0]) if problems else f"ffmpeg returned {code}"
            raise RepairError(
                f"the new file still has decode errors ({len(problems)} message(s), first: {first[:150]}) -- "
                f"the original was not changed"
            )
        advance("Verifying lengths")
        after = quick_check(target, run)
        if not after.openable or any(f.code == "read_errors" for f in after.findings):
            raise RepairError(f"the new file didn't pass the check ({after.summary()})")
        info = _probe(target, run)
        if info is None:
            raise RepairError("the new file can't be probed")
        duration = _float((info.get("format") or {}).get("duration"))
        times = _stream_times(info)
        if duration is None:
            raise RepairError("the new file has no duration")
        if "video" in times and "audio" in times:
            (v_start, v_dur), (a_start, a_dur) = times["video"], times["audio"]
            if v_dur is not None and a_dur is not None and abs(v_dur - a_dur) > _SYNC_TOLERANCE:
                raise RepairError(
                    f"audio and video lengths differ by {abs(v_dur - a_dur):.2f} s in the new file -- the original was not changed"
                )
            if v_start is not None and a_start is not None and abs(v_start - a_start) > _LENGTH_TOLERANCE:
                raise RepairError("audio and video don't start together in the new file -- the original was not changed")
        expected = plan.kept_seconds
        tolerance = _LENGTH_TOLERANCE + _LENGTH_TOLERANCE_PER_JOIN * max(len(segments) - 1, 0)
        if abs(duration - expected) > tolerance:
            raise RepairError(
                f"the new file is {_format_seconds(duration)} long, expected about {_format_seconds(expected)} -- "
                f"the original was not changed"
            )
        if segment_total and abs(duration - segment_total) > _SYNC_TOLERANCE + _LENGTH_TOLERANCE_PER_JOIN * len(segments):
            raise RepairError("the new file's length doesn't match the parts it was joined from")
    except BaseException:
        for name in scratch:
            remove_scratch(name)
        if os.path.exists(target):
            remove_scratch(target)
        raise
    return CutOutcome(
        check=after, removed=plan.spans, removed_seconds=plan.removed_seconds, new_duration=duration,
        decode_errors=0,
        verify=f"verified: full decode with 0 errors, {_format_seconds(duration)} long", note=note,
    )


def cut_out_damaged(
    path: str,
    check: CheckResult,
    run: Callable = _run,
    ffmpeg: Callable = _run_ffmpeg,
    trash: Callable[[str], None] = move_to_trash,
    restore: Callable[[str, str], str] = restore_app_metadata,
    cancel: Optional[threading.Event] = None,
    progress: Optional[Callable[[int, int], None]] = None,
    status: Optional[Callable[[str], None]] = None,
) -> CutOutcome:
    """Cuts the damaged parts out of `path` (see the module docstring): builds and verifies the copy, sends the
    original to the Recycle Bin and puts the copy in its place. Raises RepairError (the original untouched, the copy
    removed) when anything fails, CutCancelled when `cancel` was set."""
    target = repair_path(path)
    built = build_cut_copy(path, check, target, run, ffmpeg, restore, cancel, progress, status)
    if cancel is not None and cancel.is_set():
        remove_scratch(target)
        raise CutCancelled("cancelled")
    swap_in(path, target, trash)
    built.check = quick_check(path, run)
    return built
