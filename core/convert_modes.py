"""
core/convert_modes.py

The three ways Media > Convert works, all through the same safe engine (core/conversion_run.convert_file +
core/library_safety.SafeRun: verified, dry run, the shared originals policy, the undo log):

  MODE_RULES   Automatic: the conversion rules decide per file (core/conversion_plan.plan_conversion): stream copy where a
               container change is enough, a re-encode only where the rules say so. This is conversion_run.process_file.
  MODE_REMUX   Lossless remux to MP4: the streams are copied, never re-encoded. A file with a stream MP4 cannot hold (a
               bitmap subtitle, a codec only Matroska takes) is skipped with the reason: Automatic keeps it as MKV.
  MODE_H264    Re-encode to H.264/AAC MP4 at the Preferences > Transcode quality (CRF, audio bit rate, threads). A real
               re-encode: slower, a generation loss. By default the original is kept beside the new file.

Replaces the two older handlers (Remux to MP4 and Convert to MP4 (H.264)) that had their own flow, verification and
original handling.
"""

from __future__ import annotations

import os
from typing import Callable

from core import conversion_plan as cp
from core import conversion_run, transcode_settings
from core.conversion_run import ConversionOutcome
from core.ffmpeg_backend import probe_media
from core.library_safety import SafeRun

MODE_RULES = "rules"
MODE_REMUX = "remux"
MODE_H264 = "h264"
MODES = (MODE_RULES, MODE_REMUX, MODE_H264)

MODE_LABELS = {
    MODE_RULES: "Automatic (the conversion rules)",
    MODE_REMUX: "Lossless remux to MP4 (no re-encoding)",
    MODE_H264: "Re-encode to H.264 (Preferences > Transcode quality)",
}
MODE_HELP = {
    MODE_RULES: "Each file is judged by the rules in Preferences > Conversion: a legacy container becomes MP4 or MKV by "
                "stream copy, only what no container can hold is re-encoded, over-limit series episodes are scaled down.",
    MODE_REMUX: "Copies every stream into an MP4. Fast and lossless. A file with something MP4 cannot hold is skipped "
                "(Automatic would keep it as MKV).",
    MODE_H264: "A real re-encode: slower and a quality loss against the source. The original is kept beside the new "
               "file unless you untick the box below.",
}


def plan_remux(info: cp.MediaInfo) -> cp.Plan:
    """The lossless remux of any readable file into an MP4, or a plan that says why that cannot be done."""
    plan = cp.Plan()
    if info.video is None:
        plan.skip_reason = "no video stream"
        return plan
    if info.ext == ".mp4":
        plan.skip_reason = "it is already an MP4"
        return plan
    video = info.video
    if video.codec in cp.VIDEO_MKV_ONLY or video.codec in cp.VIDEO_REENCODE:
        plan.skip_reason = f"the video ({video.codec}) cannot be copied into an MP4"
        return plan
    odd_audio = [a.codec for a in info.of("audio") if a.codec not in cp.AUDIO_MP4_OK]
    if odd_audio:
        plan.skip_reason = f"the audio ({odd_audio[0]}) cannot be copied into an MP4"
        return plan
    bitmap = [t.codec for t in info.of("subtitle") if t.codec in cp.BITMAP_SUBTITLES]
    if bitmap:
        plan.skip_reason = f"the subtitles ({bitmap[0]}) cannot be copied into an MP4 (Automatic keeps the file as MKV)"
        return plan
    plan.container = ".mp4"
    plan.video = cp.V_COPY
    plan.reasons.append(f"lossless remux from {info.ext or 'the file'} to MP4")
    return plan


def plan_h264(info: cp.MediaInfo, settings: transcode_settings.TranscodeSettings) -> cp.Plan:
    """The re-encode of any file with a video stream into an H.264/AAC MP4 (subtitles MP4 cannot hold are dropped)."""
    plan = cp.Plan()
    if info.video is None:
        plan.skip_reason = "no video stream"
        return plan
    plan.container = ".mp4"
    plan.video = cp.V_X264
    plan.crf = settings.crf
    plan.audio_bitrate = settings.audio_bitrate
    plan.threads = settings.threads
    plan.audio_reencode = True
    plan.reasons.append(f"re-encode to H.264 (CRF {settings.crf}, audio {settings.audio_bitrate})")
    return plan


def output_path(source: str, plan: cp.Plan, keep: bool) -> str:
    """Where the result goes. A new extension is just a new name. A file that stays an MP4 (a re-encode of an MP4) takes
    the original's name when the original goes away, and `<name>_h264.mp4` when the original is kept."""
    stem = os.path.splitext(source)[0]
    final = stem + plan.container
    if os.path.normcase(os.path.abspath(final)) == os.path.normcase(os.path.abspath(source)) and keep:
        final = f"{stem}_h264{plan.container}"
    return final


def convert_with_mode(
    run: SafeRun,
    source: str,
    mode: str,
    conv: cp.ConversionSettings,
    *,
    keep: bool,
    is_series: bool = True,
    tags=None,
    cancel_event=None,
    transcode: transcode_settings.TranscodeSettings | None = None,
    probe: Callable[[str], cp.MediaInfo | None] = probe_media,
    **convert_kwargs,
) -> ConversionOutcome:
    """Converts one file in `mode` through `run`. `keep`: the original stays where it is (the new file gets its own
    name); it overrides the run's own originals handling for this call. MODE_RULES is conversion_run.process_file."""
    run.keep_originals = bool(keep)
    if mode == MODE_RULES:
        return conversion_run.process_file(
            run, source, conv, is_series=is_series, cancel_event=cancel_event, tags=tags, **convert_kwargs)
    info = probe(source)
    if info is None:
        return ConversionOutcome(False, message="couldn't read the file (ffprobe failed)")
    if mode == MODE_REMUX:
        plan = plan_remux(info)
    elif mode == MODE_H264:
        plan = plan_h264(info, transcode or transcode_settings.get_transcode_settings())
    else:
        raise ValueError(f"unknown conversion mode {mode!r}")
    if plan.skip_reason:
        return ConversionOutcome(False, message=plan.skip_reason)
    cover = None
    if not run.dry_run:
        cover, cover_problem = conversion_run.read_source_cover(source, info)
        if cover_problem:
            return ConversionOutcome(False, message=f"not converted, the file is untouched: {cover_problem}")
        if tags is None:
            tags = conversion_run.read_source_tags(source)
    final = output_path(source, plan, keep)
    outcome = conversion_run.convert_file(
        run, source, plan, info, conv, cover=cover, cancel_event=cancel_event, probe=probe, final=final, **convert_kwargs)
    if outcome.converted and tags is not None and not run.dry_run:
        problem = conversion_run.write_tags(outcome.final_path, tags)
        if problem:
            outcome.notes.append(f"converted, but the tags could not be rewritten onto the new file: {problem}")
    return outcome
