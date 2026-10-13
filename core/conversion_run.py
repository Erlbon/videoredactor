"""
core/conversion_run.py

Runs a conversion plan (core/conversion_plan.py) for one file through the safety layer
(core/library_safety.SafeRun): ffmpeg writes a ".part" next to the source, the result is verified against the
rules below, and only then is it swapped in (original to the Recycle Bin / quarantine / deleted, per setting).

Verification (before the swap):
  * duration within 1.5 s of the source (up to 60 s for an AVI/OGM source, whose header duration is often stale,
    provided the result's own video and audio streams agree with each other within 1.5 s);
  * the same number of video and audio tracks;
  * subtitle count = the source's kept subtitles + the added ones;
  * height <= the target when scaling;
  * a bitrate-driven result must be under 85 percent of the source size, otherwise the source is kept.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable

from core import conversion_plan as cp
from core.ffmpeg_backend import _run_ffmpeg, probe_media
from core.library_safety import KIND_REMUXED, KIND_RESIZED, SafeRun

DURATION_TOLERANCE = 1.5
STALE_HEADER_TOLERANCE = 60.0
STALE_HEADER_EXTENSIONS = (".avi", ".ogm")
CONVERT_TIMEOUT_SECONDS = 12 * 60 * 60


def expected_subtitle_count(info: cp.MediaInfo, plan: cp.Plan, added: int = 0) -> int:
    """Subtitle tracks the result must have: the source's kept ones + `added`. Into an MP4 only text subtitles
    survive (bitmap ones are dropped); into an MKV all are copied."""
    subs = info.of("subtitle")
    if plan.container == ".mp4":
        subs = [t for t in subs if t.codec in cp.TEXT_SUBTITLES]
    return len(subs) + added


def verify_result(
    source: cp.MediaInfo,
    result: cp.MediaInfo | None,
    plan: cp.Plan,
    settings: cp.ConversionSettings,
    *,
    added_subtitles: int = 0,
    result_size: int | None = None,
) -> str:
    """"" when the converted file is good, else what is wrong. Takes both probes so it needs no ffprobe."""
    if result is None:
        return "couldn't read the result back (ffprobe failed)"
    problems = []
    if len(result.of("video")) != len(source.of("video")):
        problems.append(f"video tracks: {len(source.of('video'))} before, {len(result.of('video'))} after")
    if len(result.of("audio")) != len(source.of("audio")):
        problems.append(f"audio tracks: {len(source.of('audio'))} before, {len(result.of('audio'))} after")
    want_subs = expected_subtitle_count(source, plan, added_subtitles)
    if len(result.of("subtitle")) != want_subs:
        problems.append(f"subtitle tracks: expected {want_subs}, got {len(result.of('subtitle'))}")
    if plan.scales:
        video = result.video
        if video is None or not 0 < video.height <= settings.scale_height:
            problems.append(f"height {video.height if video else '?'} is not <= {settings.scale_height}")
    if source.duration:
        if result.duration is None:
            problems.append("no duration")
        else:
            gap = abs(result.duration - source.duration)
            if gap > DURATION_TOLERANCE:
                stale_ok = source.ext in STALE_HEADER_EXTENSIONS and gap <= STALE_HEADER_TOLERANCE and _streams_agree(result)
                if not stale_ok:
                    problems.append(f"duration {result.duration:.1f}s vs {source.duration:.1f}s")
    if plan.bitrate_driven and result_size is not None and not cp.output_size_ok(source.size_bytes, result_size, settings):
        problems.append(f"result is {result_size / max(source.size_bytes, 1):.0%} of the source (must be under {settings.output_max_ratio:.0%})")
    return ", ".join(problems)


def _streams_agree(result: cp.MediaInfo) -> bool:
    """The result's own video and audio stream lengths agree (the stand-in for 'the last video and audio packets
    agree'): a source whose header lied is fine when the actual streams are consistent."""
    video, audio = result.video, (result.of("audio") or [None])[0]
    if video is None or audio is None or video.duration is None or audio.duration is None:
        return False
    return abs(video.duration - audio.duration) <= DURATION_TOLERANCE


@dataclass
class ConversionOutcome:
    converted: bool
    final_path: str = ""
    message: str = ""


def convert_file(
    run: SafeRun,
    source: str,
    plan: cp.Plan,
    info: cp.MediaInfo,
    settings: cp.ConversionSettings,
    *,
    added_subtitles: int = 0,
    cancel_event=None,
    runner: Callable = _run_ffmpeg,
    probe: Callable[[str], cp.MediaInfo | None] = probe_media,
) -> ConversionOutcome:
    """Convert `source` per `plan` through `run` (dry-run, recent-file guard, verified swap, Recycle Bin /
    quarantine). `runner`/`probe` are injectable for tests."""
    if plan.skip_reason or not plan.container:
        return ConversionOutcome(False, message=plan.skip_reason or "nothing to do")
    final = os.path.splitext(source)[0] + plan.container
    kind = KIND_RESIZED if plan.video in (cp.V_NVENC, cp.V_NVENC_SCALE) else KIND_REMUXED
    marker = "resize" if kind == KIND_RESIZED else "part"

    def build(src: str, temp: str) -> tuple[bool, str]:
        attempts = [True, False] if plan.scales else [True]  # CUDA scaling, then the software scale fallback
        message = ""
        for cuda in attempts:
            if os.path.lexists(temp):
                os.remove(temp)
            args = cp.build_ffmpeg_args(info, plan, src, temp, settings, cuda=cuda)
            code, stderr = runner(args, cancel_event=cancel_event, timeout=CONVERT_TIMEOUT_SECONDS)
            if code == 0:
                return True, ""
            message = (stderr or "").strip().splitlines()[-1] if (stderr or "").strip() else "ffmpeg failed"
            if code is None:  # cancelled or ffmpeg missing: no point retrying
                break
        return False, message

    def verify(src: str, temp: str) -> str:
        size = os.path.getsize(temp) if os.path.exists(temp) else None
        return verify_result(info, probe(temp), plan, settings, added_subtitles=added_subtitles, result_size=size)

    reason = "; ".join(plan.reasons)
    ok = run.convert(source, final, build, kind, reason, marker=marker, verify=verify)
    if not ok:
        detail = run.actions[-1].detail if run.actions else "not converted"
        return ConversionOutcome(False, message=detail)
    return ConversionOutcome(True, final_path=final, message=run.actions[-1].detail)
