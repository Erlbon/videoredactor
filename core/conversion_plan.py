"""
core/conversion_plan.py

The library conversion rules (F:\\Video cleanup, 2026-10-13) as a PURE decision engine: from what ffprobe
says about a file (MediaInfo) and the settings, decide what -- if anything -- to do (Plan), and build the
ffmpeg argument list for it. No file access, no subprocess: testable with hand-made MediaInfo.

Rules, in short (the numbers are settings, ConversionSettings):
  containers  MP4 and MKV are the only outputs. Legacy containers are converted; disc images are not touched
              unless the disc-image policy says otherwise.
  codecs      sane codecs are copied, never re-encoded. Only video no container can hold (RealVideo, Flash,
              odd WMV) is re-encoded: libx265 CRF 23, AAC 160k.
  bitrate     series episodes only (movies exempt unless apply_to_movies): too tall or too fat -> hevc_nvenc
              scaled to 720p; 720p or less with too high a bitrate -> re-encoded without scaling.
  size        files under 400 MB are ignored; a bitrate-driven result must be under 85 percent of the source.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields

from core.config import get_setting, set_setting

# --- file kinds --------------------------------------------------------------

SANE_EXTENSIONS = frozenset({".mp4", ".mkv"})
LEGACY_EXTENSIONS = frozenset({
    ".avi", ".m4v", ".mpg", ".mpeg", ".wmv", ".flv", ".f4v", ".mov", ".webm", ".ogm", ".3gp", ".asf",
    ".divx", ".rm", ".rmvb",
})
DISC_EXTENSIONS = frozenset({".vob", ".iso", ".bin", ".img"})

# Disc-image policies (ConversionSettings.disc_images).
DISC_LEAVE = "leave"  # never touched (default)
DISC_LIST = "list"  # left alone but listed for review, with size
DISC_REMUX_VOB = "remux_vob"  # a single .vob is stream-copied to MKV; ISO/BIN/IMG still only listed
DISC_POLICIES = (DISC_LEAVE, DISC_LIST, DISC_REMUX_VOB)

# --- codecs ------------------------------------------------------------------

VIDEO_COPY_ANY = frozenset({"h264", "hevc", "mpeg4", "av1", "vp9", "mpeg2video"})  # fits MP4 and MKV
VIDEO_MKV_ONLY = frozenset({"vp8", "theora", "msmpeg4v2", "msmpeg4v3", "wmv3", "vc1", "mpeg1video", "h263", "mjpeg"})
VIDEO_REENCODE = frozenset({"rv10", "rv20", "rv30", "rv40", "flv1", "wmv1", "wmv2", "msmpeg4v1", "svq1", "svq3"})

AUDIO_MP4_OK = frozenset({"aac", "mp3", "ac3", "eac3", "alac", "opus", "mp2"})
AUDIO_MKV_ONLY = frozenset({"vorbis", "flac", "dts", "truehd", "wmav1", "wmav2", "wmapro", "wmalossless", "wmavoice"})

TEXT_SUBTITLES = frozenset({"subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text"})
BITMAP_SUBTITLES = frozenset({"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "xsub"})

# Audio bitrate estimates (bits/s) when the stream has no bit_rate and no BPS tag.
AUDIO_BITRATE_ESTIMATE = {
    "ac3": 448_000, "eac3": 640_000, "dts": 1_509_000, "truehd": 3_000_000, "aac": 160_000, "mp3": 128_000,
}
DEFAULT_AUDIO_ESTIMATE = 160_000
HEAVY_AUDIO_CODECS = frozenset({"dts", "truehd", "flac"})


def is_pcm(codec: str) -> bool:
    return codec.startswith("pcm_")


# --- input: what ffprobe said ------------------------------------------------


@dataclass
class StreamInfo:
    index: int
    kind: str  # video / audio / subtitle / data / attachment
    codec: str
    bit_rate: int | None = None  # stream bit_rate, else the BPS tag
    width: int = 0
    height: int = 0
    language: str = ""
    title: str = ""
    default: bool = False
    hearing_impaired: bool = False
    attached_pic: bool = False  # cover art: "not video"
    duration: float | None = None  # the stream's own length, when ffprobe says


@dataclass
class MediaInfo:
    path: str
    size_bytes: int
    duration: float | None
    streams: list[StreamInfo] = field(default_factory=list)

    @property
    def ext(self) -> str:
        return os.path.splitext(self.path)[1].lower()

    def of(self, kind: str) -> list[StreamInfo]:
        return [s for s in self.streams if s.kind == kind and not (kind == "video" and s.attached_pic)]

    @property
    def video(self) -> StreamInfo | None:
        v = self.of("video")
        return v[0] if v else None


def parse_ffprobe(path: str, data: dict) -> MediaInfo:
    """ffprobe -show_streams -show_format -of json -> MediaInfo. Tolerates missing/null fields."""
    fmt = data.get("format") or {}
    try:
        size = int(fmt.get("size"))
    except (TypeError, ValueError):
        size = 0
    try:
        duration = float(fmt.get("duration"))
    except (TypeError, ValueError):
        duration = None
    streams = []
    for raw in data.get("streams") or []:
        if not isinstance(raw, dict):
            continue
        tags = {str(k).lower(): v for k, v in (raw.get("tags") or {}).items()}
        disp = raw.get("disposition") or {}
        bit_rate = None
        for candidate in (raw.get("bit_rate"), tags.get("bps"), tags.get("bps-eng")):
            try:
                bit_rate = int(candidate)
                break
            except (TypeError, ValueError):
                continue
        kind = raw.get("codec_type") or ""
        try:
            stream_duration = float(raw.get("duration"))
        except (TypeError, ValueError):
            stream_duration = None
        streams.append(StreamInfo(
            index=int(raw.get("index", len(streams))), kind=kind, codec=str(raw.get("codec_name") or "").lower(),
            bit_rate=bit_rate, width=int(raw.get("width") or 0), height=int(raw.get("height") or 0),
            language=str(tags.get("language") or ""), title=str(tags.get("title") or ""),
            default=bool(disp.get("default")), hearing_impaired=bool(disp.get("hearing_impaired")),
            attached_pic=bool(disp.get("attached_pic")), duration=stream_duration,
        ))
    return MediaInfo(path=path, size_bytes=size, duration=duration, streams=streams)


# --- settings ----------------------------------------------------------------


@dataclass
class ConversionSettings:
    apply_to_movies: bool = False
    min_size_mb: int = 400
    scale_height: int = 720
    scale_if_wider_than: int = 1300
    scale_size_trigger_gb: float = 1.0
    scale_video_mbps: float = 4.0
    reencode_video_mbps: float = 3.0
    reencode_video_mbps_small: float = 2.0  # when height < small_height
    small_height: int = 650
    nvenc_cq: int = 30  # 27 selectable
    output_max_ratio: float = 0.85
    many_subtitle_tracks: int = 4  # "many" subtitle tracks -> MKV
    heavy_audio_mbps: float = 0.6
    heavy_audio_tracks: int = 3
    fallback_crf: int = 23  # libx265, for video nothing can copy
    fallback_audio_bitrate: str = "160k"
    disc_images: str = DISC_LEAVE
    subtitle_languages: str = "all"  # "all", or ISO codes separated by commas


# --- output: what to do ------------------------------------------------------

V_COPY = "copy"
V_X265 = "libx265"  # video no container can hold
V_NVENC_SCALE = "nvenc_scale"  # hevc_nvenc to scale_height
V_NVENC = "nvenc"  # hevc_nvenc, no scaling


@dataclass
class Plan:
    skip_reason: str = ""  # non-empty: nothing to do, and why
    container: str = ""  # ".mp4" / ".mkv" (the output extension), "" when skipped
    video: str = V_COPY
    audio_reencode: bool = False  # AAC for audio no container can hold
    reasons: list[str] = field(default_factory=list)
    heavy_audio: bool = False
    bitrate_driven: bool = False  # the 85 percent size check applies
    video_mbps: float | None = None
    needs_review: str = ""  # a disc image listed for the user, not acted on

    @property
    def does_something(self) -> bool:
        return not self.skip_reason and bool(self.reasons)

    @property
    def scales(self) -> bool:
        return self.video == V_NVENC_SCALE


def audio_bitrate(stream: StreamInfo) -> int:
    if stream.bit_rate:
        return stream.bit_rate
    if is_pcm(stream.codec):
        return 1_536_000
    return AUDIO_BITRATE_ESTIMATE.get(stream.codec, DEFAULT_AUDIO_ESTIMATE)


def video_mbps(info: MediaInfo) -> float | None:
    """Video bitrate in Mb/s = size*8/duration minus the audio bitrate."""
    if not info.duration or info.duration <= 0 or not info.size_bytes:
        return None
    total = info.size_bytes * 8 / info.duration
    audio = sum(audio_bitrate(a) for a in info.of("audio"))
    return max(total - audio, 0) / 1_000_000


def is_heavy_audio(info: MediaInfo, s: ConversionSettings) -> bool:
    audio = info.of("audio")
    if len(audio) >= s.heavy_audio_tracks:
        return True
    if sum(audio_bitrate(a) for a in audio) / 1_000_000 > s.heavy_audio_mbps:
        return True
    return any(a.codec in HEAVY_AUDIO_CODECS or is_pcm(a.codec) for a in audio)


def _mkv_only_audio(codec: str) -> bool:
    return codec in AUDIO_MKV_ONLY or is_pcm(codec)


def plan_conversion(
    info: MediaInfo, settings: ConversionSettings | None = None, *, is_series: bool = True, extra_subtitles: int = 0,
) -> Plan:
    """The decision for one file. `extra_subtitles` = external subtitle tracks about to be added (an MP4 then
    becomes an MKV)."""
    s = settings or ConversionSettings()
    plan = Plan()
    ext = info.ext

    if ext in DISC_EXTENSIONS:
        if s.disc_images == DISC_REMUX_VOB and ext == ".vob":
            plan.container, plan.video = ".mkv", V_COPY
            plan.reasons.append("disc image .vob: stream copy to MKV (policy)")
            return plan
        plan.skip_reason = f"disc image ({ext}) is left alone"
        if s.disc_images != DISC_LEAVE:
            plan.needs_review = f"disc image {os.path.basename(info.path)} ({info.size_bytes / 1048576:.0f} MB): needs a manual decision"
        return plan
    if ext not in SANE_EXTENSIONS and ext not in LEGACY_EXTENSIONS:
        plan.skip_reason = f"{ext or 'no extension'} is not a video container this app converts"
        return plan

    video = info.video
    if video is None:
        plan.skip_reason = "no video stream"
        return plan
    audio = info.of("audio")
    subs = info.of("subtitle")
    plan.heavy_audio = is_heavy_audio(info, s)

    # --- codecs: what can be copied, which container they need ---
    if video.codec in VIDEO_REENCODE:
        plan.video = V_X265
        plan.reasons.append(f"video {video.codec} can't be copied into MP4/MKV: re-encode to HEVC (CRF {s.fallback_crf})")
    elif video.codec not in VIDEO_COPY_ANY and video.codec not in VIDEO_MKV_ONLY:
        plan.skip_reason = f"unknown video codec {video.codec or '?'}: left alone"
        return plan
    why_mkv: list[str] = []  # why the container must be MKV; only a reason to ACT when the file isn't one already
    needs_mkv = video.codec in VIDEO_MKV_ONLY
    unknown_audio = [a for a in audio if a.codec not in AUDIO_MP4_OK and not _mkv_only_audio(a.codec)]
    if unknown_audio:
        plan.audio_reencode = True
        plan.reasons.append(f"audio {unknown_audio[0].codec} can't be copied: AAC {s.fallback_audio_bitrate}")
    needs_mkv = needs_mkv or any(_mkv_only_audio(a.codec) for a in audio)
    if needs_mkv:
        why_mkv.append("a stream only MKV can hold")
    if len(subs) >= s.many_subtitle_tracks:
        needs_mkv = True
        why_mkv.append(f"{len(subs)} subtitle tracks: MKV")
    if extra_subtitles:
        needs_mkv = True
        why_mkv.append(f"{extra_subtitles} external subtitle(s) to add: MKV")
    plan.container = ".mkv" if needs_mkv else ".mp4"

    # --- container change? ---
    if ext in LEGACY_EXTENSIONS:
        plan.reasons.append(f"legacy container {ext}")
        plan.reasons += why_mkv
    elif ext == ".mkv":
        plan.container = ".mkv"  # an MKV is already sane; it is never moved to MP4 here
    elif plan.container == ".mkv":
        plan.reasons.append(f"{ext} to .mkv")
        plan.reasons += why_mkv

    # --- bitrate / size rules ---
    plan.video_mbps = video_mbps(info)
    exempt = (not is_series and not s.apply_to_movies) or info.size_bytes < s.min_size_mb * 1048576
    mbps = plan.video_mbps
    if not exempt and mbps is not None and plan.video == V_COPY:
        tall = video.height > s.scale_height or video.width > s.scale_if_wider_than
        if tall:
            if info.size_bytes > s.scale_size_trigger_gb * 1073741824 or mbps > s.scale_video_mbps:
                plan.video, plan.bitrate_driven = V_NVENC_SCALE, True
                plan.reasons.append(f"{video.height}p at {mbps:.1f} Mb/s, {info.size_bytes / 1073741824:.1f} GB: scale to {s.scale_height}p")
        else:
            limit = s.reencode_video_mbps_small if video.height < s.small_height else s.reencode_video_mbps
            if mbps > limit:
                plan.video, plan.bitrate_driven = V_NVENC, True
                plan.reasons.append(f"{video.height}p at {mbps:.1f} Mb/s (limit {limit:g}): re-encode")
    return plan


# --- ffmpeg ------------------------------------------------------------------


def _mp4_subtitle_ok(codec: str) -> bool:
    return codec in TEXT_SUBTITLES


def build_ffmpeg_args(
    info: MediaInfo, plan: Plan, src: str, out: str, settings: ConversionSettings | None = None, *, cuda: bool = True,
) -> list[str]:
    """The ffmpeg argument LIST (never a shell string). Streams are mapped explicitly: cover art is not video,
    data/timecode tracks are dropped (-dn), audio and kept subtitles are copied. For an MP4 target text
    subtitles become mov_text and bitmap ones are dropped; for MKV they are copied. `cuda` False = software
    scale fallback."""
    s = settings or ConversionSettings()
    args = ["ffmpeg", "-v", "error", "-y"]
    scaling = plan.scales
    if scaling and cuda:
        args += ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda"]
    args += ["-fflags", "+genpts", "-i", src]
    mp4 = plan.container == ".mp4"

    for v in info.of("video"):
        args += ["-map", f"0:{v.index}"]
    for a in info.of("audio"):
        args += ["-map", f"0:{a.index}"]
    kept_subs = [t for t in info.of("subtitle") if not mp4 or _mp4_subtitle_ok(t.codec)]
    for t in kept_subs:
        args += ["-map", f"0:{t.index}"]
    args += ["-dn"]

    if plan.video == V_COPY:
        args += ["-c:v", "copy"]
    elif plan.video == V_X265:
        args += ["-c:v", "libx265", "-crf", str(s.fallback_crf), "-tag:v", "hvc1"]
    else:
        args += ["-c:v", "hevc_nvenc", "-preset", "p6", "-tune", "hq", "-rc", "vbr", "-cq", str(s.nvenc_cq),
                 "-b:v", "0", "-spatial-aq", "1", "-rc-lookahead", "32", "-tag:v", "hvc1"]
        if scaling:
            args += ["-vf", f"scale_cuda=-2:{s.scale_height}" if cuda else f"scale=-2:{s.scale_height}"]
    if plan.audio_reencode or plan.video == V_X265:
        args += ["-c:a", "aac", "-b:a", s.fallback_audio_bitrate]
    else:
        args += ["-c:a", "copy"]
    if kept_subs:
        args += ["-c:s", "mov_text" if mp4 else "copy"]
    args += [out]
    return args


def output_size_ok(source_bytes: int, output_bytes: int, settings: ConversionSettings | None = None) -> bool:
    """A bitrate-driven result must be under output_max_ratio of the source, otherwise keep the source."""
    s = settings or ConversionSettings()
    return source_bytes > 0 and output_bytes < source_bytes * s.output_max_ratio


# --- persistence ([conversion] in videoredactor_settings.ini) ---------------------------


def get_conversion_settings(get=get_setting) -> ConversionSettings:
    """Every field is a setting; a missing or hand-damaged value falls back to the default."""
    defaults = ConversionSettings()
    values = {}
    for f in fields(ConversionSettings):
        default = getattr(defaults, f.name)
        raw = get("conversion", f.name, "")
        if raw == "":
            continue
        try:
            if isinstance(default, bool):
                values[f.name] = raw == "1"
            elif isinstance(default, int):
                values[f.name] = int(raw)
            elif isinstance(default, float):
                values[f.name] = float(raw)
            else:
                values[f.name] = raw
        except ValueError:
            continue
    result = ConversionSettings(**values)
    if result.disc_images not in DISC_POLICIES:
        result.disc_images = DISC_LEAVE
    return result


def set_conversion_settings(s: ConversionSettings, put=set_setting) -> None:
    for f in fields(ConversionSettings):
        value = getattr(s, f.name)
        put("conversion", f.name, ("1" if value else "0") if isinstance(value, bool) else str(value))
