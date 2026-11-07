"""
core/lyrics_embed.py

Get Lyrics' "Embed the timed lyrics as a subtitle track": the LRCLIB lyrics, already turned into an SRT (offset
applied), muxed INTO the music video as a subtitle track named "Lyrics". Lossless (nothing is re-encoded) and through
the same verified swap as every conversion (core/library_safety.SafeRun.convert): the new file is written to a temp
name beside the original, checked (video still there, the same audio tracks, the subtitle tracks one more than before
minus a replaced "Lyrics" track, the same length) and only then takes the original's name; the original goes to the
Recycle Bin or the quarantine folder, as Library safety says.

  MKV   mkvmerge (MKVToolNix, which every MKV in this app needs anyway): copies every track, tag, chapter and
        attachment as it is and adds the .srt as an S_TEXT/UTF8 track.
  MP4   ffmpeg: copies the video (not the poster picture), audio and subtitle streams and the chapters, adds the
        .srt as a mov_text track; the app's tags and the poster are written back onto the result before it is
        verified (ffmpeg renames tags on the way and does not carry the poster, see core/conversion_run).

An earlier "Lyrics" track is replaced, not doubled. The new track is the default subtitle only when the video has no
other subtitle track. Qt-free.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import Callable, Optional

from core import conversion_run, library_safety
from core.external_tools import FFMPEG, MKVTOOLNIX, is_tool_available
from core.ffmpeg_backend import _run_ffmpeg, _stream_summary
from core.mkv_backend import MKV_TOOL_TIMEOUT_SECONDS, _identify
from core.subtitle_ingest import language_code
from redactor_common.core.subprocess_utils import run_tool

LYRICS_TRACK_NAME = "Lyrics"
FFMPEG_TIMEOUT_SECONDS = 1800


@dataclass
class EmbedResult:
    ok: bool
    message: str
    replaced: int = 0  # earlier "Lyrics" tracks that were taken out
    actions: list = field(default_factory=list)  # the SafeRun report


def unsupported_reason(path: str) -> str:
    """"" when the file can take the track, else why not (shown as the checkbox's tooltip)."""
    ext = os.path.splitext(str(path))[1].lower()
    if ext == ".mkv":
        return "" if is_tool_available(MKVTOOLNIX) else f"needs MKVToolNix ({MKVTOOLNIX.download_url})"
    if ext == ".mp4":
        return "" if is_tool_available(FFMPEG) else f"needs ffmpeg ({FFMPEG.download_url})"
    return "only MP4 and MKV files can hold a subtitle track"


def track_language(language: str) -> str:
    """The ISO 639-2 code the track is tagged with ("en" -> "eng"); "und" when the dialog's choice isn't a language."""
    return language_code(language) or "und"


# --- the tracks there are now --------------------------------------------------------------------------------------


@dataclass
class SubtitleTrack:
    track_id: int   # MKV: the mkvmerge track id; MP4: the position among the subtitle streams (ffmpeg's s:N)
    name: str


def mkv_subtitle_tracks(info: dict) -> list[SubtitleTrack]:
    """The subtitle tracks of `mkvmerge -J` output."""
    out = []
    for track in info.get("tracks") or []:
        if isinstance(track, dict) and track.get("type") == "subtitles":
            props = track.get("properties") or {}
            out.append(SubtitleTrack(int(track.get("id", -1)), str(props.get("track_name") or "")))
    return out


def mp4_subtitle_tracks(streams: list[dict]) -> list[SubtitleTrack]:
    """The subtitle streams of ffprobe's stream list, numbered as ffmpeg's s:N."""
    out = []
    for stream in streams:
        if isinstance(stream, dict) and stream.get("codec_type") == "subtitle":
            tags = stream.get("tags") or {}
            name = tags.get("title") or tags.get("handler_name") or ""
            out.append(SubtitleTrack(len(out), str(name)))
    return out


def _is_lyrics(track: SubtitleTrack) -> bool:
    return track.name.strip().casefold() == LYRICS_TRACK_NAME.casefold()


def _ffprobe_streams(path: str) -> list[dict]:
    import json  # noqa: PLC0415

    from core.ffmpeg_backend import _run  # noqa: PLC0415

    result = _run(["ffprobe", "-v", "error", "-show_entries", "stream=index,codec_type:stream_tags", "-of", "json", path])
    if result is None or result.returncode != 0:
        return []
    try:
        return list((json.loads(result.stdout) or {}).get("streams") or [])
    except (ValueError, TypeError):
        return []


# --- the commands ---------------------------------------------------------------------------------------------------


def mkvmerge_args(source: str, srt_path: str, temp: str, language: str, drop: list[int], default: bool) -> list[str]:
    """mkvmerge: everything of `source` except the `drop` subtitle tracks, plus the .srt as the "Lyrics" track."""
    args = ["mkvmerge", "-o", temp]
    if drop:
        args += ["--subtitle-tracks", "!" + ",".join(str(i) for i in drop)]
    args += [source,
             "--language", f"0:{language}", "--track-name", f"0:{LYRICS_TRACK_NAME}",
             "--default-track-flag", f"0:{'yes' if default else 'no'}", "--sub-charset", "0:UTF-8", srt_path]
    return args


def ffmpeg_args(source: str, srt_path: str, temp: str, language: str, drop: list[int], kept: int, default: bool) -> list[str]:
    """ffmpeg: the real video streams (0:V, not the poster picture), all audio, the subtitles except `drop`, the
    chapters, and the .srt as mov_text track number `kept` (the count of subtitle tracks kept)."""
    args = ["ffmpeg", "-v", "error", "-y", "-i", source, "-i", srt_path,
            "-map", "0:V", "-map", "0:a?", "-map", "0:s?"]
    for index in drop:
        args += ["-map", f"-0:s:{index}"]
    args += ["-map", "1:0", "-map_chapters", "0", "-c", "copy", "-c:s", "mov_text",
             f"-metadata:s:s:{kept}", f"language={language}",
             f"-metadata:s:s:{kept}", f"handler_name={LYRICS_TRACK_NAME}",
             f"-metadata:s:s:{kept}", f"title={LYRICS_TRACK_NAME}",
             f"-disposition:s:{kept}", "default" if default else "0",
             "-movflags", "+faststart", temp]
    return args


# --- the run ----------------------------------------------------------------------------------------------------------


def _verifier(added: int, run: library_safety.SafeRun, probe: Callable) -> Callable[[str, str], str]:
    """SafeRun's check for this job: rule 3 (video, the same audio tracks, the same length) with the subtitle count
    expected to change by `added` (1 minus the replaced Lyrics tracks)."""
    def verify(source: str, temp: str) -> str:
        before = probe(source)
        if before is not None:
            counts = dict(before[0])
            counts["subtitle"] = counts.get("subtitle", 0) + added
            before = (counts, before[1])
        return library_safety.verify_streams(before, probe(temp), run.tolerance_seconds, run.tolerance_fraction)
    return verify


def embed_lyrics_track(
    path: str,
    srt_text: str,
    language: str,
    run: library_safety.SafeRun,
    *,
    mkv_identify: Callable[[str], dict] = _identify,
    mp4_streams: Callable[[str], list[dict]] = _ffprobe_streams,
    mkv_runner: Optional[Callable[[list[str]], tuple[Optional[int], str]]] = None,
    ffmpeg_runner: Callable = _run_ffmpeg,
    probe: Callable = _stream_summary,
    cancel_event=None,
) -> EmbedResult:
    """Muxes `srt_text` into `path` as the "Lyrics" subtitle track (see the module text). Never raises for an
    ordinary failure: the result says what happened, and the original is untouched unless `ok`."""
    path = str(path)
    if not srt_text.strip():
        return EmbedResult(False, "there are no timed lyrics to embed")
    reason = unsupported_reason(path)
    if reason:
        return EmbedResult(False, reason)
    mkv = path.lower().endswith(".mkv")
    lang = track_language(language)
    tracks = mkv_subtitle_tracks(mkv_identify(path)) if mkv else mp4_subtitle_tracks(mp4_streams(path))
    drop = [t.track_id for t in tracks if _is_lyrics(t)]
    kept = len(tracks) - len(drop)
    default = kept == 0

    work = tempfile.mkdtemp(prefix="videoredactor-lyrics-")
    srt_path = os.path.join(work, "lyrics.srt")
    try:
        with open(srt_path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(srt_text)
        if mkv:
            build = _mkv_build(srt_path, lang, drop, default, mkv_runner or _run_mkvmerge)
        else:
            build = _mp4_build(srt_path, lang, drop, kept, default, ffmpeg_runner, cancel_event)
        try:
            done = run.convert(path, path, build, library_safety.KIND_REMUXED, "embed the lyrics as a subtitle track",
                               verify=_verifier(1 - len(drop), run, probe))
        except library_safety.SafetyError as exc:
            return EmbedResult(False, f"the original is kept: {exc}", actions=run.actions)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    last = run.actions[-1] if run.actions else None
    if not done or last is None or not last.done:
        why = last.detail if last is not None else "nothing was done"
        if last is not None and last.verb == "skip" and last.reason and "left alone" in why:
            why = last.reason
        return EmbedResult(False, why, actions=run.actions)
    what = f"embedded the lyrics as a subtitle track ({lang})"
    if drop:
        what += ", replacing the earlier Lyrics track"
    return EmbedResult(True, what, replaced=len(drop), actions=run.actions)


def _run_mkvmerge(args: list[str]) -> tuple[Optional[int], str]:
    from redactor_common.core.tool_locator import get_executable_path  # noqa: PLC0415

    result = run_tool([get_executable_path(args[0])] + args[1:], timeout=MKV_TOOL_TIMEOUT_SECONDS * 6)
    return result.returncode, (result.stdout or "") + (result.stderr or "")


def _mkv_build(srt_path: str, lang: str, drop: list[int], default: bool, runner) -> Callable[[str, str], tuple[bool, str]]:
    def build(source: str, temp: str) -> tuple[bool, str]:
        code, output = runner(mkvmerge_args(source, srt_path, temp, lang, drop, default))
        if code in (0, 1):  # 1 = warnings only; the file is written
            return True, ""
        return False, _last_line(output) or f"mkvmerge failed (exit code {code})"
    return build


def _mp4_build(srt_path: str, lang: str, drop: list[int], kept: int, default: bool, runner, cancel_event):
    def build(source: str, temp: str) -> tuple[bool, str]:
        tags = conversion_run.read_source_tags(source)
        cover = None
        try:
            from core.video_file import VideoFile  # noqa: PLC0415
            from pathlib import Path  # noqa: PLC0415

            cover = VideoFile(path=Path(source)).read_cover()
        except Exception as exc:  # noqa: BLE001 -- an unreadable poster would be lost: stop here
            return False, f"the poster could not be read ({exc}); embedding would lose it"
        code, stderr = runner(ffmpeg_args(source, srt_path, temp, lang, drop, kept, default),
                              cancel_event=cancel_event, timeout=FFMPEG_TIMEOUT_SECONDS)
        if code != 0:
            return False, _last_line(stderr) or ("ffmpeg failed" if code is not None else "ffmpeg did not finish")
        if cover:
            problem = conversion_run.restore_cover(temp, cover)
            if problem:
                return False, f"the poster could not be put back: {problem}"
        if tags is not None:
            problem = conversion_run.write_tags(temp, tags)
            if problem:
                return False, f"the tags could not be put back: {problem}"
        return True, ""
    return build


def _last_line(text: str) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return lines[-1] if lines else ""
