"""
core/bitrate_bands.py

The quality colour of the Bitrate column (Qt-free). The shared vocabulary (Band, BandResult, the tints, codec_factor,
tooltip helpers) and the painter are redactor_common's (core/quality_bands.py, gui/quality_colour.py); this module is the
VIDEO profile: bits per pixel per frame, codec factors, and the conversion limit.

Measure. bpp = video bit rate / (width * height * frames per second); the video stream's own bit rate (ffprobe's
bit_rate or the BPS tag), else the file's total rate minus the audio the rules already estimate
(conversion_plan.video_mbps_from). A codec factor relative to H.264 turns it into an H.264-equivalent:

    hevc/h265 0.6, av1 0.5, vp9 0.65, h264 1.0, mpeg4/xvid/divx/msmpeg4/wmv/vc1 1.3, mpeg2 1.6, others 1.0

Bands (all files are coloured: series, movies, music videos, clips):
    OVER   (orange) the file's video rate is above the CONVERSION LIMIT of Convert to MP4/MKV (Rules) for its
           resolution. It is NOT a second copy of the numbers: it asks conversion_plan.bitrate_rule(), the very function
           plan_conversion() uses, so the table and the rules can never disagree (tests/test_bitrate_bands.py checks it
           against the plan engine on generated files). A movie is judged as if it were a series (information only: the
           tooltip says so unless 'apply to movies' is on); a file the rules skip (under the minimum size, a codec or
           container they never touch) is not OVER.
    POOR   (yellow) the H.264-equivalent bpp is below `poor_bpp` (default 0.05). "Check it", not "bad": animation and
           low-motion content compresses better.
    OK     (green) otherwise.
    UNKNOWN (no colour) the width, height, frame rate or bit rate is missing; the tooltip says which.
`good_bpp` (default 0.10) only shapes the tooltip's wording ("good from about X Mb/s"). Both thresholds are editable in
Preferences > Conversion (validated 0.01..0.5, poor < good).

Reference values (H.264, 24 fps, poor / good): 480p about 0.5 / 1.0 Mb/s, 720p 1.1 / 2.2, 1080p 2.5 / 5, 4K 10 / 20;
HEVC about 40 percent lower.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from redactor_common.core.quality_bands import Band, BandResult, codec_factor, join_tooltip, unknown

from core import conversion_plan as cp
from core.config import get_setting, set_setting

SECTION = "bitrate_colours"
POOR_BPP = 0.05
GOOD_BPP = 0.10
BPP_RANGE = (0.01, 0.5)

VIDEO_CODEC_FACTORS: dict[str, float] = {
    "h264": 1.0, "avc": 1.0, "avc1": 1.0,
    "hevc": 0.6, "h265": 0.6, "av1": 0.5, "vp9": 0.65,
    "mpeg4": 1.3, "xvid": 1.3, "divx": 1.3, "msmpeg4": 1.3, "wmv": 1.3, "vc1": 1.3,
    "mpeg2": 1.6, "mpeg2video": 1.6,
}
CODEC_NAMES = {"h264": "H.264", "avc": "H.264", "hevc": "HEVC", "h265": "HEVC", "av1": "AV1", "vp9": "VP9",
               "mpeg4": "MPEG-4", "xvid": "Xvid", "divx": "DivX", "msmpeg4": "MS MPEG-4", "wmv": "WMV", "vc1": "VC-1",
               "mpeg2": "MPEG-2"}
REFERENCE_NOTE = "Yellow means 'check it', not 'bad': animation and low-motion content compresses better."


@dataclass(frozen=True)
class BandSettings:
    poor_bpp: float = POOR_BPP
    good_bpp: float = GOOD_BPP


def valid_thresholds(poor: float, good: float) -> bool:
    low, high = BPP_RANGE
    return low <= poor <= high and low <= good <= high and poor < good


def get_band_settings(get=get_setting) -> BandSettings:
    """The two thresholds from [bitrate_colours]; a missing, damaged or inconsistent pair gives the defaults."""
    try:
        poor = float(get(SECTION, "poor_bpp", "") or POOR_BPP)
        good = float(get(SECTION, "good_bpp", "") or GOOD_BPP)
    except ValueError:
        return BandSettings()
    return BandSettings(poor, good) if valid_thresholds(poor, good) else BandSettings()


def set_band_settings(settings: BandSettings, put=set_setting) -> None:
    if not valid_thresholds(settings.poor_bpp, settings.good_bpp):
        raise ValueError(f"the thresholds must lie within {BPP_RANGE[0]}..{BPP_RANGE[1]} and poor must be below good")
    put(SECTION, "poor_bpp", f"{settings.poor_bpp:g}")
    put(SECTION, "good_bpp", f"{settings.good_bpp:g}")


@dataclass(frozen=True)
class BitrateInput:
    """What the colour needs to know about one video."""

    width: int = 0
    height: int = 0
    fps: Optional[float] = None
    codec: str = ""
    video_bps: Optional[int] = None  # the video stream's own bit rate, when known
    size_bytes: Optional[int] = None
    duration: Optional[float] = None
    audio_bps: float = 0.0  # the audio streams' rates (their own, else the rules' estimates)
    ext: str = ".mp4"
    is_series: bool = True


def codec_label(codec: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "", str(codec or "").lower())
    for name in sorted(CODEC_NAMES, key=len, reverse=True):
        if key.startswith(name):
            return CODEC_NAMES[name]
    return str(codec).upper() if codec else "video"


def _mbps(bits_per_second: float) -> str:
    """'1.5 Mb/s', '3 Mb/s' (a trailing .0 dropped)."""
    text = f"{bits_per_second / 1_000_000:.1f}"
    return (text[:-2] if text.endswith(".0") else text) + " Mb/s"


def _missing(inp: BitrateInput) -> list[str]:
    missing = []
    if inp.width <= 0 or inp.height <= 0:
        missing.append("the resolution")
    if not inp.fps or inp.fps <= 0:
        missing.append("the frame rate")
    if not _video_bps(inp):
        missing.append("the video bit rate")
    return missing


def _video_bps(inp: BitrateInput) -> Optional[float]:
    if inp.video_bps and inp.video_bps > 0:
        return float(inp.video_bps)
    mbps = cp.video_mbps_from(inp.size_bytes, inp.duration, inp.audio_bps)
    return mbps * 1_000_000 if mbps else None


def classify(inp: BitrateInput, bands: BandSettings = BandSettings(),
             conv: Optional[cp.ConversionSettings] = None) -> BandResult:
    """The band of one video (see the module docstring)."""
    conv = conv or cp.get_conversion_settings()
    name = codec_label(inp.codec)
    bps = _video_bps(inp)

    notes = []
    if not inp.is_series and not conv.apply_to_movies:
        notes.append("Movies are exempt from the conversion rules unless 'apply to movies' is on: this colour is "
                     "information only.")

    # OVER first, and independent of the frame rate and the width: exactly the cases where the conversion rules would
    # re-encode this file for its bit rate (their own function, with their own measure: size and duration).
    plan_mbps = cp.video_mbps_from(inp.size_bytes, inp.duration, inp.audio_bps)
    rule = None
    if (inp.height > 0 or inp.width > 0) and cp.rules_cover(inp.ext, inp.codec):
        rule = cp.bitrate_rule(inp.height, inp.width, inp.size_bytes or 0, plan_mbps, conv, is_series=True)

    missing = _missing(inp)
    if not missing:
        assert bps is not None and inp.fps
        factor = codec_factor(inp.codec, VIDEO_CODEC_FACTORS)
        bpp = bps / (inp.width * inp.height * inp.fps)
        effective = bpp / factor
        pixels_per_second = inp.width * inp.height * inp.fps
        poor_bps = bands.poor_bpp * factor * pixels_per_second
        good_bps = bands.good_bpp * factor * pixels_per_second
        head = f"{inp.height}p {name}, {_mbps(bps)} video ({bpp:.2f} bpp"
        head += f", {name}-equivalent {effective:.2f})." if abs(factor - 1.0) > 1e-9 else ")."
        guide = f"Poor below {_mbps(poor_bps)} for {name}; good from about {_mbps(good_bps)}."
    else:
        head = f"{inp.height}p {name}" + (f", {_mbps(bps)} video." if bps else ".")
        guide = ""

    if rule is not None and rule.over:
        shown = _mbps(bps) if bps else _mbps((plan_mbps or 0) * 1_000_000)
        return BandResult(
            Band.OVER, f"{shown} (over the limit)",
            join_tooltip(head, guide,
                         f"Above the conversion limit: Convert to MP4/MKV (Rules) would re-encode it ({rule.reason}).",
                         *notes))
    if missing:
        return unknown("No colour: " + " and ".join(missing) + " of this file " + ("is" if len(missing) == 1 else "are")
                       + " unknown.")
    skipped = f"Not over the conversion limit ({rule.exempt})." if rule is not None and rule.exempt and         "movies" not in rule.exempt else ""
    if effective < bands.poor_bpp:
        return BandResult(Band.POOR, f"{_mbps(bps)} (poor)",
                          join_tooltip(head, guide, f"Poor: below {_mbps(poor_bps)}.", REFERENCE_NOTE, skipped, *notes))
    return BandResult(Band.OK, f"{_mbps(bps)} (fine)", join_tooltip(head, guide, "Fine.", REFERENCE_NOTE, skipped, *notes))


# -- from the app's own data ------------------------------------------------------------------------------------------

def input_from_media_info(info: cp.MediaInfo, fps: Optional[float], is_series: bool = True) -> BitrateInput:
    """A BitrateInput from a conversion MediaInfo (for tests and tools that already probed the file)."""
    video = info.video
    return BitrateInput(
        width=video.width if video else 0, height=video.height if video else 0, fps=fps,
        codec=video.codec if video else "", video_bps=video.bit_rate if video else None,
        size_bytes=info.size_bytes, duration=info.duration,
        audio_bps=float(sum(cp.audio_bitrate(a) for a in info.of("audio"))), ext=info.ext, is_series=is_series,
    )


def _number(text: object) -> Optional[float]:
    try:
        value = float(str(text).strip())
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def input_from_file(vf, is_series: bool) -> BitrateInput:
    """A BitrateInput from a loaded VideoFile's probed fields (core/ffmpeg_backend.probe_technical_info)."""
    md = vf.metadata
    width = height = 0
    match = re.fullmatch(r"\s*(\d+)\s*x\s*(\d+)\s*", md.resolution or "")
    if match:
        width, height = int(match.group(1)), int(match.group(2))
    video_bps = _number(md.video_bitrate)
    audio = _number(md.audio_bitrate_total) or 0.0
    try:
        size = vf.size_bytes
    except OSError:
        size = None
    return BitrateInput(
        width=width, height=height, fps=_number(md.frame_rate), codec=md.video_codec or "",
        video_bps=int(video_bps) if video_bps else None, size_bytes=size, duration=md.duration_seconds,
        audio_bps=audio, ext=vf.path.suffix.lower(), is_series=is_series,
    )
