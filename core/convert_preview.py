"""
core/convert_preview.py

What Media > Convert would do, in words, for two places (Qt-free, so it is tested without a window):

  * the dry-run table (decision 12B, 2026-11-18): one PreviewRow per file, built from the outcome of a dry run
    (conversion_run.ConversionOutcome carries the probe and the plan): route, resolution, codec, video Mb/s, action;
  * the "Would convert" column of the main table (decision 13A): would_convert() judges a loaded row from the
    technical fields the table already has (no ffprobe), with the same bitrate rule the conversion uses.

The column is an estimate: it knows the container, the video codec and the bit rate, not every stream, so a remux an
MP4/MKV needs for one of its streams (a bitmap subtitle, a codec only MKV holds) shows up in the dry run only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from core import conversion_plan as cp

ROUTE_SKIP = "Skip"
ROUTE_REMUX = "Remux"
ROUTE_SHRINK = "Shrink"
ROUTE_REENCODE = "Re-encode"

WOULD_NO = "no"
WOULD_REMUX = "remux"
WOULD_REENCODE = "re-encode"


def route_of(plan: cp.Plan | None) -> str:
    """The route of a plan: Skip, Remux (stream copy), Shrink (a bitrate re-encode) or Re-encode (the legacy x265 or
    the H.264 mode)."""
    if plan is None or plan.skip_reason or not plan.does_something:
        return ROUTE_SKIP
    if plan.video in (cp.V_NVENC, cp.V_NVENC_SCALE):
        return ROUTE_SHRINK
    if plan.video in (cp.V_X265, cp.V_X264):
        return ROUTE_REENCODE
    return ROUTE_REMUX


def action_of(plan: cp.Plan | None, settings: cp.ConversionSettings, message: str = "") -> str:
    """The action in a few words: '-> 720p HEVC', 're-encode, HEVC', '-> .mp4, copy', or why it is skipped."""
    route = route_of(plan)
    if route == ROUTE_SKIP:
        return (plan.skip_reason if plan is not None and plan.skip_reason else message) or "nothing to do"
    assert plan is not None
    if plan.scales:
        return f"→ {plan.scale_to(settings)}p HEVC, {plan.container}"
    if plan.video in (cp.V_NVENC, cp.V_X265):
        return f"re-encode, HEVC {plan.container}"
    if plan.video == cp.V_X264:
        return f"re-encode, H.264 {plan.container}"
    return f"→ {plan.container}, copy"


@dataclass(frozen=True)
class PreviewRow:
    path: str
    route: str
    resolution: str
    codec: str
    mbps: float | None
    action: str
    changes: bool  # a real run would do something: ticked by default, kept by "Only files that would change"

    @property
    def name(self) -> str:
        return os.path.basename(self.path)


def preview_row(path: str, outcome, settings: cp.ConversionSettings) -> PreviewRow:
    """One dry-run table row from a dry-run outcome (conversion_run.ConversionOutcome with `media` and `plan`). A
    file the dry run would convert has outcome.converted True; one it skips (busy, the target exists) keeps the
    run's reason as its action."""
    info, plan = outcome.media, outcome.plan
    video = info.video if info is not None else None
    resolution = f"{video.height}p" if video is not None and video.height else ""
    codec = video.codec if video is not None else ""
    mbps = plan.video_mbps if plan is not None and plan.video_mbps is not None else (
        cp.video_mbps(info) if info is not None else None)
    changes = bool(outcome.converted)
    route = route_of(plan) if changes else ROUTE_SKIP
    action = action_of(plan, settings) if changes else (outcome.message or action_of(plan, settings))
    return PreviewRow(path, route, resolution, codec, mbps, action, changes)


def would_convert(
    ext: str, codec: str, height: int, width: int, size_bytes: int | None, mbps: float | None, kind: str,
    settings: cp.ConversionSettings,
) -> str:
    """The "Would convert" column (decision 13A): '-> 720p' (a scale), 're-encode', 'remux' or 'no', judged like
    plan_conversion() does from what the table knows. Disc images and unknown containers are 'no'."""
    ext = (ext or "").lower()
    codec = (codec or "").lower()
    if ext not in cp.SANE_EXTENSIONS and ext not in cp.LEGACY_EXTENSIONS:
        return WOULD_NO
    known = codec in cp.VIDEO_COPY_ANY or codec in cp.VIDEO_MKV_ONLY
    if codec in cp.VIDEO_REENCODE or (codec and not known and ext in cp.LEGACY_EXTENSIONS):
        return WOULD_REENCODE
    if not known:
        return WOULD_NO
    if height > 0 or width > 0:
        rule = cp.bitrate_rule(height, width, size_bytes or 0, mbps, settings, kind=kind, codec=codec)
        if rule.over:
            return f"→ {rule.target_height}p" if rule.scale else WOULD_REENCODE
    return WOULD_REMUX if ext in cp.LEGACY_EXTENSIONS else WOULD_NO
