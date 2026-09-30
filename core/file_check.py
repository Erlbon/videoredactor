"""
core/file_check.py

Operations > Check Files... -- a QUICK health check of a video file, and
Repair for what a lossless remux can fix.

The check (seconds per file, no decoding):
1. ffprobe reads the container: an unopenable file (an MP4 cut off
   before its index, garbage) is DAMAGED and can't be repaired here.
2. ffmpeg reads every packet without decoding (-c copy to the null
   muxer): read errors ("File ended prematurely", "partial file") and
   how far it got. A file that stops well short of its declared length
   ENDS EARLY -- typically an interrupted download or copy.
3. The file's own structure, read directly (no tool): an MKV without a
   seek index (Cues -- seeking is slow or impossible, often from a
   recording or stream capture), an MP4 whose index (moov) comes after
   the media (it can't start playing until fully downloaded, the
   "faststart" problem), and no recorded duration.
4. Tracks: several audio tracks with no default or more than one
   default; audio/subtitle tracks with no language (reported only --
   which language it is can't be guessed).

Repair = a lossless remux with ffmpeg (-map 0 -c copy: every track,
chapter and attachment): writes the container fresh, which
rebuilds the MKV seek index and duration, puts an MP4's index first,
fixes default-track flags, and for a file that ends early or has read
errors keeps everything readable up to a second before the break (the
last packet there is usually half-written; what's missing stays
missing). ffmpeg carries standard tags over but drops this app's
own custom ones (director, cast, studio, collection, rating... --
found by testing), so the app's metadata is then written onto the
repaired copy with the app's own writers (restore_app_metadata) and
compared field by field. The repaired copy is written next to the
original, checked, and only then swapped in; the original goes to the
Recycle Bin, never deleted. The check needs only ffmpeg; restoring an
MKV's tags needs MKVToolNix, as editing them does.
"""

from __future__ import annotations

import json
import os
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import dataclasses

from redactor_common.core.trash import TrashError, move_to_trash

from core.external_tools import MKVTOOLNIX, is_tool_available
from core.ffmpeg_backend import REMUX_TIMEOUT_SECONDS, _run

DAMAGED = "damaged"      # the content itself is incomplete or unreadable
REPAIRABLE = "repairable"  # fixed by a lossless remux
NOTE = "note"            # worth knowing, nothing to repair

# A file "ends early" when readable content stops more than this short
# of its declared length (seconds, or fraction of the length).
_SHORT_BY_SECONDS = 2.0
_SHORT_BY_FRACTION = 0.02
# How much of a damaged file's readable end a repair drops (see repair()).
_DAMAGED_TAIL_SECONDS = 1.0


@dataclass
class Finding:
    code: str
    severity: str
    message: str


@dataclass
class CheckResult:
    findings: list[Finding] = field(default_factory=list)
    declared_seconds: Optional[float] = None
    readable_seconds: Optional[float] = None
    openable: bool = True

    @property
    def status(self) -> str:
        """For the Status column: DAMAGED, REPAIRABLE, NOTE or CHECKED OK."""
        severities = {f.severity for f in self.findings}
        for severity, label in ((DAMAGED, "DAMAGED"), (REPAIRABLE, "REPAIRABLE"), (NOTE, "NOTE")):
            if severity in severities:
                return label
        return "CHECKED OK"

    @property
    def can_repair(self) -> bool:
        """A remux helps: the file opens and something is fixable -- a
        damaged-but-openable file keeps what's readable."""
        return self.openable and any(f.severity in (DAMAGED, REPAIRABLE) for f in self.findings)

    def summary(self) -> str:
        return "; ".join(f.message for f in self.findings) or "No problems found"


# ---------------------------------------------------------------------------
# File structure, read directly
# ---------------------------------------------------------------------------

_EBML_SEGMENT = 0x18538067
_EBML_SEEKHEAD = 0x114D9B74
_EBML_SEEK = 0x4DBB
_EBML_SEEK_ID = 0x53AB
_EBML_SEEK_POSITION = 0x53AC
_EBML_CUES = 0x1C53BB6B
_EBML_CLUSTER = 0x1F43B675


def _read_vint(handle, keep_marker: bool) -> tuple[Optional[int], int, bool]:
    """(value, length, all_ones) of one EBML variable-length integer at
    the current position; value None at end of file."""
    first = handle.read(1)
    if not first:
        return None, 0, False
    byte = first[0]
    length = 1
    mask = 0x80
    while length <= 8 and not byte & mask:
        mask >>= 1
        length += 1
    if length > 8:
        raise ValueError("invalid EBML length")
    rest = handle.read(length - 1)
    if len(rest) != length - 1:
        return None, 0, False
    value = byte if keep_marker else byte & (mask - 1)
    for b in rest:
        value = (value << 8) | b
    all_ones = not keep_marker and value == (1 << (7 * length)) - 1
    return value, length, all_ones


def mkv_has_seek_index(path: str, max_elements: int = 200_000) -> Optional[bool]:
    """Whether the MKV has Cues (its seek index), found through the
    SeekHead or by walking the Segment's top-level elements; None if the
    file isn't Matroska at all."""
    size = os.path.getsize(path)
    with open(path, "rb") as handle:
        if handle.read(4) != bytes.fromhex("1a45dfa3"):  # the EBML signature
            return None
        handle.seek(0)
        header_id, _n, _o = _read_vint(handle, keep_marker=True)
        header_size, _n, _o = _read_vint(handle, keep_marker=False)
        handle.seek(header_size, os.SEEK_CUR)
        segment_id, _n, _o = _read_vint(handle, keep_marker=True)
        if segment_id != _EBML_SEGMENT:
            return None
        _segment_size, _n, _o = _read_vint(handle, keep_marker=False)
        segment_start = handle.tell()
        for _ in range(max_elements):
            element_id, _n, _o = _read_vint(handle, keep_marker=True)
            if element_id is None:
                return False
            element_size, _n, unknown_size = _read_vint(handle, keep_marker=False)
            if element_size is None:
                return False
            body = handle.tell()
            if element_id == _EBML_CUES:
                return True
            if element_id == _EBML_SEEKHEAD and not unknown_size:
                if _seekhead_points_at_cues(handle.read(element_size), segment_start, size):
                    return True
            if unknown_size:
                # A live-written file (stream capture, piped output):
                # elements of unknown size can't be skipped, and such
                # files don't carry an index.
                return False
            handle.seek(body + element_size)
            if body + element_size >= size:
                return False
    return False


def _seekhead_points_at_cues(data: bytes, segment_start: int, file_size: int) -> bool:
    import io

    stream = io.BytesIO(data)
    while True:
        seek_id, _n, _o = _read_vint(stream, keep_marker=True)
        if seek_id is None:
            return False
        seek_size, _n, _o = _read_vint(stream, keep_marker=False)
        if seek_size is None:
            return False
        body = stream.read(seek_size)
        if seek_id != _EBML_SEEK:
            continue
        inner = io.BytesIO(body)
        target_id = position = None
        while True:
            child_id, _n, _o = _read_vint(inner, keep_marker=True)
            if child_id is None:
                break
            child_size, _n, _o = _read_vint(inner, keep_marker=False)
            value = inner.read(child_size or 0)
            if child_id == _EBML_SEEK_ID:
                target_id = int.from_bytes(value, "big")
            elif child_id == _EBML_SEEK_POSITION:
                position = int.from_bytes(value, "big")
        # The pointer only counts if the index is really there (a
        # truncated file's SeekHead still points past its end).
        if target_id == _EBML_CUES and position is not None and segment_start + position < file_size:
            return True


def mp4_index_at_end(path: str) -> Optional[bool]:
    """True when the MP4's index (moov) comes after its media (mdat);
    None if neither is found among the top-level boxes."""
    size = os.path.getsize(path)
    order = []
    with open(path, "rb") as handle:
        position = 0
        while position + 8 <= size and len(order) < 2:
            handle.seek(position)
            header = handle.read(8)
            box_size, box_type = struct.unpack(">I4s", header)
            if box_size == 1:
                box_size = struct.unpack(">Q", handle.read(8))[0]
            elif box_size == 0:
                box_size = size - position
            if box_size < 8:
                break
            if box_type in (b"moov", b"mdat") and box_type not in order:
                order.append(box_type)
            position += box_size
    if not order:
        return None
    return order[0] == b"mdat"


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------

_TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")


def _last_time(stderr: str) -> Optional[float]:
    matches = _TIME_RE.findall(stderr.replace("\r", "\n"))
    if not matches:
        return None
    hours, minutes, seconds = matches[-1]
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _error_lines(stderr: str) -> list[str]:
    lines = [line.strip() for line in stderr.replace("\r", "\n").splitlines()]
    return [line for line in lines if line and not line.startswith("frame=") and "time=" not in line]


def _clean(line: str) -> str:
    """ffmpeg's "[in#0/matroska,webm @ 0x...] File ended prematurely" ->
    "File ended prematurely"."""
    return re.sub(r"^\[[^\]]*\]\s*", "", line)


def _format_seconds(seconds: float) -> str:
    seconds = int(round(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def quick_check(path: str, run: Callable = _run) -> CheckResult:
    """The quick check (see the module docstring). `run` is ffmpeg_backend's
    tool runner (returns None when the tool is missing or times out)."""
    result = CheckResult()
    suffix = Path(path).suffix.lower()

    probe = run([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration:stream=index,codec_type:stream_tags=language:stream_disposition=default",
        "-of", "json", path,
    ])
    if probe is None:
        result.openable = False
        result.findings.append(Finding("no_tool", NOTE, "Not checked: ffprobe isn't available"))
        return result
    if probe.returncode != 0:
        result.openable = False
        reason = _clean((_error_lines(probe.stderr) or ["unreadable"])[-1])
        reason = reason.replace(f"{path}: ", "")
        result.findings.append(Finding("unreadable", DAMAGED, f"Can't be opened ({reason}) -- not repairable here"))
        return result
    try:
        info = json.loads(probe.stdout or "{}")
    except json.JSONDecodeError:
        info = {}
    duration = (info.get("format") or {}).get("duration")
    try:
        result.declared_seconds = float(duration) if duration not in (None, "N/A") else None
    except ValueError:
        result.declared_seconds = None

    read = run(["ffmpeg", "-v", "error", "-stats", "-i", path, "-map", "0", "-c", "copy", "-f", "null", "-"],
               timeout=REMUX_TIMEOUT_SECONDS)
    if read is not None:
        result.readable_seconds = _last_time(read.stderr)
        errors = _error_lines(read.stderr)
        if errors:
            first = _clean(errors[0])
            if len(first) > 90:  # ffmpeg's container-level detail is long and technical
                first = first[:87].rstrip() + "..."
            more = f" (+{len(errors) - 1} more)" if len(errors) > 1 else ""
            result.findings.append(Finding("read_errors", DAMAGED, f"Read errors: {first}{more}"))
    declared, readable = result.declared_seconds, result.readable_seconds
    if declared and readable is not None:
        short_by = declared - readable
        if short_by > max(_SHORT_BY_SECONDS, declared * _SHORT_BY_FRACTION):
            result.findings.append(Finding(
                "ends_early", DAMAGED,
                f"Ends early: plays {_format_seconds(readable)} of {_format_seconds(declared)}",
            ))
    if declared is None:
        result.findings.append(Finding("no_duration", REPAIRABLE, "No duration recorded"))

    try:
        if suffix == ".mkv" and mkv_has_seek_index(path) is False:
            result.findings.append(Finding("no_index", REPAIRABLE, "No seek index (seeking is slow)"))
        if suffix in (".mp4", ".m4v") and mp4_index_at_end(path):
            result.findings.append(Finding(
                "index_at_end", REPAIRABLE, "Index at the end (can't start playing until fully loaded)",
            ))
    except (OSError, ValueError, struct.error):
        pass

    streams = info.get("streams") or []
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    defaults = sum(1 for s in audio if (s.get("disposition") or {}).get("default"))
    if len(audio) > 1 and defaults == 0:
        result.findings.append(Finding("no_default_audio", REPAIRABLE, "No default audio track"))
    elif defaults > 1:
        result.findings.append(Finding("several_default_audio", REPAIRABLE, f"{defaults} default audio tracks"))
    unlabelled = [
        s for s in streams
        if s.get("codec_type") in ("audio", "subtitle")
        and (s.get("tags") or {}).get("language", "und").lower() in ("", "und")
    ]
    if unlabelled:
        kinds = sorted({s["codec_type"] for s in unlabelled})
        result.findings.append(Finding(
            "no_language", NOTE, f"{len(unlabelled)} {'/'.join(kinds)} track(s) without a language",
        ))
    return result


# ---------------------------------------------------------------------------
# Repair
# ---------------------------------------------------------------------------

class RepairError(Exception):
    pass


def _compared_fields(meta) -> dict:
    return {f.name: getattr(meta, f.name) for f in dataclasses.fields(meta) if f.compare}


def restore_app_metadata(original: str, repaired: str) -> str:
    """Writes the original's metadata (as this app reads it) onto the
    repaired copy and checks every field reads back the same. Returns a
    note for the user ("" when all went fine); raises RepairError when
    the copy would lose metadata the original has."""
    suffix = Path(original).suffix.lower()
    if suffix in (".mp4", ".m4v"):
        from core.mp4_backend import read_mp4_cover, read_mp4_metadata, write_mp4_cover, write_mp4_metadata

        meta = read_mp4_metadata(original)
        cover = read_mp4_cover(original)
        write_mp4_metadata(repaired, meta)
        if cover:
            write_mp4_cover(repaired, cover, is_png=cover[:8] == b"\x89PNG\r\n\x1a\n")
        if _compared_fields(read_mp4_metadata(repaired)) != _compared_fields(meta):
            raise RepairError("the repaired copy's metadata didn't match the original's")
        if cover and read_mp4_cover(repaired) != cover:
            raise RepairError("the repaired copy's cover didn't match the original's")
        return ""
    if suffix == ".mkv":
        if not is_tool_available(MKVTOOLNIX):
            return "MKVToolNix isn't installed, so only the tags ffmpeg carries over were kept"
        from core.mkv_backend import read_mkv_metadata, write_mkv_metadata

        meta = read_mkv_metadata(original)
        done = write_mkv_metadata(repaired, meta)
        if done.returncode not in (0, 1):  # mkvpropedit: 1 = finished with warnings
            raise RepairError(f"couldn't restore the tags: {(done.stderr or '').strip()[:200]}")
        if _compared_fields(read_mkv_metadata(repaired)) != _compared_fields(meta):
            raise RepairError("the repaired copy's metadata didn't match the original's")
        return ""
    return ""


def repair_path(path: str) -> str:
    """Where the repaired copy is written before it replaces the original."""
    p = Path(path)
    return str(p.with_name(f"{p.stem}.repairing{p.suffix}"))


@dataclass
class RepairOutcome:
    check: CheckResult  # the repaired file's own check
    note: str = ""      # e.g. tags that couldn't be restored


def repair(
    path: str,
    before: CheckResult,
    run: Callable = _run,
    trash: Callable[[str], None] = move_to_trash,
    restore: Callable[[str, str], str] = restore_app_metadata,
) -> RepairOutcome:
    """Remuxes `path` losslessly (see the module docstring), restores the
    app's metadata onto the copy, checks it, sends the original to the
    Recycle Bin and puts the copy in its place. Raises RepairError (the
    original untouched, the copy removed) when anything fails."""
    if not before.can_repair:
        raise RepairError("nothing a remux can fix")
    target = repair_path(path)
    if os.path.exists(target):
        raise RepairError(f"{os.path.basename(target)} already exists -- remove it first")
    suffix = Path(path).suffix.lower()
    extra = ["-movflags", "+faststart"] if suffix in (".mp4", ".m4v") else []
    codes = {f.code for f in before.findings}
    if codes & {"no_default_audio", "several_default_audio"}:
        extra += ["-disposition:a", "0", "-disposition:a:0", "default"]
    # A damaged file's last packet before the break is usually half-
    # written: stop a second short of where reading failed, so the copy
    # is clean (a remux can't restore what's missing anyway).
    cut = None
    if codes & {"read_errors", "ends_early"} and before.readable_seconds:
        cut = max(before.readable_seconds - _DAMAGED_TAIL_SECONDS, 0.0)
        extra += ["-t", f"{cut:.3f}"]

    def remux(maps: list[str]) -> Optional[str]:
        done = run(["ffmpeg", "-v", "error", "-y", "-i", path, *maps, "-c", "copy", "-map_metadata", "0",
                    *extra, target], timeout=REMUX_TIMEOUT_SECONDS)
        if done is None:
            return "ffmpeg isn't available (or stopped responding)"
        if done.returncode != 0 or not os.path.exists(target):
            return _clean((_error_lines(done.stderr) or ["remux failed"])[-1])
        return None

    # Everything first; then without data streams (timecode, MP4 text
    # chapters), which a fresh container may refuse; then without cover
    # pictures stored as video streams ("0:V" = real video only; an
    # unparsable cover stops the muxer -- restore_app_metadata puts an
    # MP4's cover back, an MKV's cover is an attachment and stays).
    attempts = [
        ["-map", "0"],
        ["-map", "0", "-map", "-0:d"],
        ["-map", "0:V", "-map", "0:a?", "-map", "0:s?", "-map", "0:t?"],
    ]
    try:
        problem = None
        for maps in attempts:
            if os.path.exists(target):
                os.remove(target)
            problem = remux(maps)
            if not problem:
                break
        if problem:
            raise RepairError(problem)
        try:
            note = restore(path, target)
        except RepairError:
            raise
        except Exception as exc:  # mutagen/OSError/timeout while copying tags: the original stays
            raise RepairError(f"couldn't restore the tags: {exc}") from exc
        after = quick_check(target, run)
        if not after.openable or any(f.code == "read_errors" for f in after.findings):
            raise RepairError(f"the repaired copy didn't pass the check ({after.summary()})")
        expected = cut if cut is not None else (before.readable_seconds or before.declared_seconds)
        if expected and after.readable_seconds is not None and after.readable_seconds < expected - _SHORT_BY_SECONDS:
            raise RepairError(
                f"the repaired copy is shorter than the readable original "
                f"({_format_seconds(after.readable_seconds)} vs {_format_seconds(expected)})"
            )
        try:
            trash(path)
        except TrashError as exc:
            raise RepairError(str(exc)) from exc
    except BaseException:
        if os.path.exists(target):
            os.remove(target)
        raise
    try:
        os.replace(target, path)
    except OSError as exc:
        # The original is already in the Recycle Bin: keep the repaired
        # copy under its temporary name rather than lose both.
        raise RepairError(
            f"the original is in the Recycle Bin, but the repaired copy couldn't take its name "
            f"({exc}); it's kept as {os.path.basename(target)}"
        ) from exc
    return RepairOutcome(check=quick_check(path, run), note=note)
