"""
core/conversion_run.py

Runs a conversion plan (core/conversion_plan.py) for one file through the safety layer
(core/library_safety.SafeRun): ffmpeg writes a ".redact-part" next to the source, the result is verified against the
rules below, and only then is it swapped in (original to the Recycle Bin / quarantine / deleted, per setting).

Verification (before the swap):
  * duration within the Library safety tolerance of the source (Preferences > Library safety: the larger of
    "Duration tolerance" in seconds and its share of the length; the run's SafeRun carries both), and up to 60 s
    for an AVI/OGM source, whose header duration is often stale, provided the result's own video and audio
    streams agree with each other within 1.5 s;
  * the same number of video and audio tracks;
  * subtitle count = the source's kept subtitles + the added ones;
  * height <= the target when scaling;
  * a bitrate-driven result must be under 85 percent of the source size, otherwise the source is kept.
"""

from __future__ import annotations

import copy
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from core import container_check
from core import conversion_plan as cp
from core import encoder_detect, history
from core.ffmpeg_backend import _run_ffmpeg, probe_media
from core import mt_registry
from core.external_tools import MKVTOOLNIX, is_tool_available
from core import subtitle_ingest as si
from core.library_safety import KIND_CLEANUP, KIND_REMUXED, KIND_RESIZED, PART_MARKER, RESIZE_MARKER, SafeRun
from core.video_file import SUPPORTED_EXTENSIONS, VideoFile

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
    tolerance_seconds: float = DURATION_TOLERANCE,
    tolerance_fraction: float = 0.0,
) -> str:
    """"" when the converted file is good, else what is wrong. Takes both probes so it needs no ffprobe.
    The duration may differ by the larger of `tolerance_seconds` and `tolerance_fraction` of the source's
    length (the Library safety settings, passed in by convert_file from its SafeRun)."""
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
            allowed = max(tolerance_seconds, source.duration * tolerance_fraction)
            if gap > allowed:
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


def decode_problem(path: str) -> str:
    """"" when ffmpeg decodes every stream of `path` with no error at all (a full decode to the null muxer), else the
    first error line. Used to verify a remux of a file whose container was wrong."""
    from core.ffmpeg_backend import _run  # noqa: PLC0415

    result = _run(["ffmpeg", "-v", "error", "-i", path, "-map", "0:V", "-map", "0:a?", "-f", "null", "-"], timeout=CONVERT_TIMEOUT_SECONDS)
    if result is None:
        return "the full decode could not run (ffmpeg missing or timed out)"
    lines = [line.strip() for line in (result.stderr or "").splitlines() if line.strip()]
    if result.returncode != 0 or lines:
        return f"the result does not decode cleanly: {lines[0] if lines else 'ffmpeg failed'}"
    return ""


@dataclass
class ConversionOutcome:
    converted: bool
    final_path: str = ""
    message: str = ""
    notes: list[str] = field(default_factory=list)  # FYIs for the report (an .srt that was left alone, ...)


def convert_file(
    run: SafeRun,
    source: str,
    plan: cp.Plan,
    info: cp.MediaInfo,
    settings: cp.ConversionSettings,
    *,
    added: list[cp.AddedSub] | None = None,
    cover: bytes | None = None,
    cancel_event=None,
    runner: Callable = _run_ffmpeg,
    probe: Callable[[str], cp.MediaInfo | None] = probe_media,
    resolve_encoder: Callable[[cp.ConversionSettings], str] = encoder_detect.resolve_encoder,
    decode_check: Callable[[str], str] = decode_problem,
) -> ConversionOutcome:
    """Convert `source` per `plan` through `run` (dry-run, recent-file guard, verified swap, Recycle Bin /
    quarantine). `runner`/`probe`/`resolve_encoder` are injectable for tests. A bitrate re-encode uses the
    encoder settings.encoder resolves to (core/encoder_detect.py: "auto" = hevc_nvenc only when it works on
    this computer, else libx265); the GPU decode/scale attempt (then the software-scale retry) only with
    settings.hw_decode on and nvenc.

    `cover` is the source's embedded poster (read_source_cover): ffmpeg does not carry it, so it is put back into
    the new file BEFORE the swap and read back; when that fails the conversion fails and the original is kept."""
    added = added or []
    added_subtitles = len(added)
    if plan.skip_reason or not plan.container:
        return ConversionOutcome(False, message=plan.skip_reason or "nothing to do")
    final = os.path.splitext(source)[0] + plan.container
    kind = KIND_RESIZED if plan.video in (cp.V_NVENC, cp.V_NVENC_SCALE) else KIND_REMUXED
    marker = RESIZE_MARKER if kind == KIND_RESIZED else PART_MARKER

    encoder = None
    if plan.video in (cp.V_NVENC, cp.V_NVENC_SCALE) and not run.dry_run:
        encoder = resolve_encoder(settings)
    gpu = plan.scales and settings.hw_decode and encoder == cp.ENCODER_NVENC

    def build(src: str, temp: str) -> tuple[bool, str]:
        attempts = [True, False] if gpu else [False]  # CUDA decode + scaling, then the software scale retry
        message = ""
        for cuda in attempts:
            if os.path.lexists(temp):
                os.remove(temp)
            args = cp.build_ffmpeg_args(info, plan, src, temp, settings, cuda=cuda, added=added, encoder=encoder)
            code, stderr = runner(args, cancel_event=cancel_event, timeout=CONVERT_TIMEOUT_SECONDS)
            if code == 0:
                if cover:
                    problem = restore_cover(temp, cover)
                    if problem:
                        return False, f"the embedded poster could not be carried over ({problem})"
                return True, ""
            message = (stderr or "").strip().splitlines()[-1] if (stderr or "").strip() else "ffmpeg failed"
            if code is None:  # cancelled or ffmpeg missing: no point retrying
                break
        return False, message

    def verify(src: str, temp: str) -> str:
        size = os.path.getsize(temp) if os.path.exists(temp) else None
        result_info = probe(temp)
        problem = verify_result(
            info, result_info, plan, settings, added_subtitles=added_subtitles, result_size=size,
            tolerance_seconds=run.tolerance_seconds, tolerance_fraction=run.tolerance_fraction,
        )
        if not problem and plan.wrong_container:
            # A wrong-container fix is only done when the result really is the container its name says, and the
            # whole result decodes with zero errors (the content was intact before: it must stay so).
            expected = container_check.container_key_from_format_name(result_info.format_name) if result_info else None
            want = ".mkv" if plan.container == ".mkv" else ".mp4"
            if expected != ("matroska" if want == ".mkv" else "mp4"):
                problem = f"the result is not a real {want[1:].upper()} file (ffprobe says {result_info.format_name if result_info else '?'})"
            else:
                problem = decode_check(temp)
        if not problem and cover:
            problem = verify_cover(temp, cover)
        return problem

    reason = "; ".join(plan.reasons)
    ok = run.convert(source, final, build, kind, reason, marker=marker, verify=verify)
    if not ok:
        detail = run.actions[-1].detail if run.actions else "not converted"
        return ConversionOutcome(False, message=detail)
    if not run.dry_run and os.path.normcase(os.path.abspath(final)) != os.path.normcase(os.path.abspath(source)):
        # The swap succeeded and the file has a new name (extension): its Tools > History entries follow it.
        history.record_move(source, final)
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


def _cover_like_attachment(info: cp.MediaInfo) -> bool:
    """An MKV attachment that looks like the poster (cover.jpg/png/webp, any case)."""
    from core.mkv_backend import _COVER_NAME_RE  # noqa: PLC0415

    return any(s.kind == "attachment" and _COVER_NAME_RE.match(s.filename or "") for s in info.streams)


def read_source_cover(path: str, info: cp.MediaInfo) -> tuple[bytes | None, str]:
    """(the embedded poster of an MP4/MKV, "") read BEFORE a conversion replaces the file, (None, "") when it has
    none, or (None, what is wrong) when it has one that can't be read -- the conversion must then not go ahead,
    because ffmpeg does not carry the picture and the original is about to be replaced. `info` (ffprobe) is the
    witness that a picture exists: an attached picture stream (MP4) or a cover-named attachment (MKV)."""
    ext = os.path.splitext(path)[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        return None, ""
    known = any(s.attached_pic for s in info.streams) or (ext == ".mkv" and _cover_like_attachment(info))
    try:
        if ext == ".mkv" and not is_tool_available(MKVTOOLNIX):
            raise RuntimeError(f"MKVToolNix is not installed ({MKVTOOLNIX.download_url})")
        data = VideoFile(path=Path(path)).read_cover()
    except Exception as exc:  # noqa: BLE001 -- unreadable: only a problem when a picture is known to be there
        if known:
            return None, f"the file has an embedded poster that could not be read ({exc}); converting would lose it"
        return None, ""
    if not data and known:
        return None, "the file has an embedded poster that could not be read; converting would lose it"
    return data or None, ""


def restore_cover(path: str, cover: bytes) -> str:
    """Puts `cover` into the converted file at `path` (MP4 covr atom or MKV cover attachment) and reads it back.
    "" when it is there, else the problem."""
    try:
        return VideoFile(path=Path(path)).embed_cover_now(cover)
    except Exception as exc:  # noqa: BLE001
        return str(exc)


def verify_cover(path: str, cover: bytes) -> str:
    """"" when the file at `path` carries exactly `cover`, else a problem for the verification step."""
    try:
        found = VideoFile(path=Path(path)).read_cover()
    except Exception as exc:  # noqa: BLE001
        return f"the embedded poster could not be read back ({exc})"
    return "" if found == cover else "the embedded poster is missing from the result"


def read_source_tags(path: str):
    """The app's tags of an MP4/MKV (a copy), read BEFORE a conversion replaces it; None for any other container
    (an AVI carries none) or when the file can't be read. A conversion through ffmpeg carries tags over only
    under ffmpeg's own names (an MP4 'show' becomes 'SHOW', not this app's 'SHOW_TITLE'), so they are written
    again afterwards by write_tags()."""
    if os.path.splitext(path)[1].lower() not in SUPPORTED_EXTENSIONS:
        return None
    vf = VideoFile(path=Path(path))
    vf.load()
    if vf.load_error:
        return None
    tags = copy.deepcopy(vf.metadata)
    tags.scan_stamp = ""  # a Check Files stamp describes the OLD file's bytes: the new file is unchecked
    return tags


def write_tags(final_path: str, metadata) -> str:
    """Writes `metadata` onto the converted file with the app's own writer (write, re-read, compare). Returns
    "" when it stuck, else what went wrong."""
    try:
        vf = VideoFile(path=Path(final_path), metadata=copy.deepcopy(metadata))
        vf.save_error = ""
        # Not journaled in Tools > History: this VideoFile was never loaded, so it has no baseline (the tags are
        # carried over unchanged and the original is in the Recycle Bin / quarantine).
        vf.save()
        return vf.save_error
    except Exception as exc:  # noqa: BLE001 -- a tag problem must not hide that the conversion itself worked
        return str(exc)


def process_file(
    run: SafeRun,
    source: str,
    settings: cp.ConversionSettings,
    *,
    is_series: bool = True,
    probe: Callable[[str], cp.MediaInfo | None] = probe_media,
    embedded_texts: Callable[[cp.MediaInfo, str], list[str]] = embedded_subtitle_texts,
    reader=None,
    carry_tags: bool = True,
    tags=None,
    **convert_kwargs,
) -> ConversionOutcome:
    """The whole job for one file: probe, find and prepare the external .srt files, plan, convert (verified swap),
    then remove the ingested .srt files. A file with nothing to do returns converted=False with the reason.

    `carry_tags` (default on): the app's tags (`tags`, or read from the source when None) are written onto the
    converted file afterwards, because ffmpeg renames them on the way (see read_source_tags). The Redact step
    passes False: it rewrites the tags from its own working copy."""
    info = probe(source)
    if info is None:
        return ConversionOutcome(False, message="couldn't read the file (ffprobe failed)")
    # Plan first: a file the rules won't touch (disc image, unknown codec) needs no sidecar work at all.
    plan = cp.plan_conversion(info, settings, is_series=is_series)
    if plan.needs_review:
        return ConversionOutcome(False, message=plan.needs_review)
    if plan.skip_reason:
        return ConversionOutcome(False, message=plan.skip_reason)
    notes: list[str] = []
    externals = si.find_external_subs(source)
    readable = []
    for ext_sub in externals:
        try:
            si.load(ext_sub, reader=reader)
        except (si.SubtitleDecodeError, OSError) as exc:
            notes.append(f"{os.path.basename(ext_sub.path)} left alone, not ingested: {exc}")
            continue
        readable.append(ext_sub)
    chosen = si.prepare(
        readable, video_had_subtitles=bool(info.of("subtitle")), whitelist=si.language_whitelist(settings.subtitle_languages),
    )
    if chosen and info.of("subtitle"):  # the embedded-text extraction (ffmpeg) only when there is something to compare
        embedded = embedded_texts(info, source)
        chosen = [s for s in chosen if not si.duplicate_of_embedded(s.text, embedded)]
    if chosen:
        plan = cp.plan_conversion(info, settings, is_series=is_series, extra_subtitles=len(chosen))
    if not plan.does_something:
        return ConversionOutcome(False, message=plan.skip_reason or "nothing to do", notes=notes)
    cover = None
    if not run.dry_run:
        cover, cover_problem = read_source_cover(source, info)
        if cover_problem:
            return ConversionOutcome(False, message=f"not converted, the file is untouched: {cover_problem}", notes=notes)
    if carry_tags and tags is None and not run.dry_run:
        tags = read_source_tags(source)
    with tempfile.TemporaryDirectory(prefix="videoredactor-subs-") as folder:
        added = []
        for n, s in enumerate(chosen):
            path = os.path.join(folder, f"{n:02d}.srt")
            if not run.dry_run:
                with open(path, "w", encoding="utf-8", newline="") as f:
                    f.write(s.text)
            added.append(cp.AddedSub(path, s.language, mt_registry.track_title(s.text, s.title), s.default, s.hearing_impaired))
        outcome = convert_file(run, source, plan, info, settings, added=added, cover=cover, probe=probe, **convert_kwargs)
    outcome.notes = notes + outcome.notes
    if outcome.converted and carry_tags and tags is not None and not run.dry_run:
        problem = write_tags(outcome.final_path, tags)
        if problem:
            outcome.notes.append(f"converted, but the tags could not be rewritten onto the new file: {problem}")
    if outcome.converted:
        # Only the files that went into the video are removed; a skipped duplicate (or an unreadable file)
        # stays where it is.
        for s in chosen:
            run.dispose(s.path, KIND_CLEANUP, "subtitle ingested into the video")
    return outcome
