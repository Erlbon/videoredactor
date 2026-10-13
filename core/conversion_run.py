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
import tempfile
from dataclasses import dataclass
from typing import Callable

from core import conversion_plan as cp
from core.ffmpeg_backend import _run_ffmpeg, probe_media
from core import subtitle_ingest as si
from core.library_safety import KIND_CLEANUP, KIND_REMUXED, KIND_RESIZED, SafeRun

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
    added: list[cp.AddedSub] | None = None,
    cancel_event=None,
    runner: Callable = _run_ffmpeg,
    probe: Callable[[str], cp.MediaInfo | None] = probe_media,
) -> ConversionOutcome:
    """Convert `source` per `plan` through `run` (dry-run, recent-file guard, verified swap, Recycle Bin /
    quarantine). `runner`/`probe` are injectable for tests."""
    added = added or []
    added_subtitles = len(added)
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
            args = cp.build_ffmpeg_args(info, plan, src, temp, settings, cuda=cuda, added=added)
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


def embedded_subtitle_texts(info: cp.MediaInfo, source: str) -> list[str]:
    """The text of each embedded text subtitle (ffmpeg -> SubRip on stdout), for the duplicate check."""
    from core.ffmpeg_backend import _run  # noqa: PLC0415

    texts = []
    for track in info.of("subtitle"):
        if track.codec not in cp.TEXT_SUBTITLES:
            continue
        result = _run(["ffmpeg", "-v", "error", "-i", source, "-map", f"0:{track.index}", "-f", "srt", "-"], timeout=300)
        if result is not None and result.returncode == 0 and result.stdout:
            texts.append(result.stdout)
    return texts


def process_file(
    run: SafeRun,
    source: str,
    settings: cp.ConversionSettings,
    *,
    is_series: bool = True,
    probe: Callable[[str], cp.MediaInfo | None] = probe_media,
    embedded_texts: Callable[[cp.MediaInfo, str], list[str]] = embedded_subtitle_texts,
    reader=None,
    **convert_kwargs,
) -> ConversionOutcome:
    """The whole job for one file: probe, find and prepare the external .srt files, plan, convert (verified swap),
    then remove the ingested .srt files. A file with nothing to do returns converted=False with the reason."""
    info = probe(source)
    if info is None:
        return ConversionOutcome(False, message="couldn't read the file (ffprobe failed)")
    externals = [] if info.ext in cp.DISC_EXTENSIONS else si.find_external_subs(source)
    for ext_sub in externals:
        si.load(ext_sub, reader=reader)
    chosen = si.prepare(
        externals, video_had_subtitles=bool(info.of("subtitle")), whitelist=si.language_whitelist(settings.subtitle_languages),
    )
    if chosen and info.of("subtitle"):
        embedded = embedded_texts(info, source)
        chosen = [s for s in chosen if not si.duplicate_of_embedded(s.text, embedded)]
    plan = cp.plan_conversion(info, settings, is_series=is_series, extra_subtitles=len(chosen))
    if plan.needs_review:
        return ConversionOutcome(False, message=plan.needs_review)
    if not plan.does_something:
        return ConversionOutcome(False, message=plan.skip_reason or "nothing to do")
    with tempfile.TemporaryDirectory(prefix="videoredactor-subs-") as folder:
        added = []
        for n, s in enumerate(chosen):
            path = os.path.join(folder, f"{n:02d}.srt")
            if not run.dry_run:
                with open(path, "w", encoding="utf-8", newline="") as f:
                    f.write(s.text)
            added.append(cp.AddedSub(path, s.language, s.title, s.default, s.hearing_impaired))
        outcome = convert_file(run, source, plan, info, settings, added=added, probe=probe, **convert_kwargs)
    if outcome.converted:
        # Only the files that went into the video are removed; a skipped duplicate stays where it is.
        for s in chosen:
            run.dispose(s.path, KIND_CLEANUP, "subtitle ingested into the video")
    return outcome
