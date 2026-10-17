"""
core/deep_scan.py

Deep scan (Media > Check Files... > "Deep scan (reads the whole file)"): finds the parts of a media file that were
never downloaded or were overwritten with zeros. A torrent or a broken copy leaves whole blocks (often 256 KiB,
aligned to 256 KiB) of 0x00 bytes INSIDE the media data. The index (moov) and the container still look fine, so the
quick check says "repairable", but the pictures and sound in those blocks are gone, and a remux cannot bring them back.

How it works:
1. The file is read ONCE, in 8 MB chunks (streaming, so it works over a network share), with progress and cancel
   callbacks, and every run of zero bytes of at least `min_run` (64 KiB) inside the media data is recorded. For an MP4
   that is the mdat box(es) (free/skip atoms and the moov are never scanned: legitimate zero padding there is no
   finding). For a Matroska file it is the clusters, from the first Cluster up to the Cues/Tags: reported as
   "approximate" (the element walk is not exact when an element header itself is zeroed). Other containers: the
   whole file, also approximate.
2. When there are zero runs, an ffprobe packet listing (pos, size, pts, dts, duration, flags per stream; no decoding,
   the index is enough for an MP4) maps the bytes to packets. A packet is DAMAGED when a zero run covers more than a
   small part of it, or its first bytes are zero, or (video) its NAL length walk fails. Only packets are damaged: a
   zero run that lies in no packet (padding between packets) is reported but harmless.
3. Damaged packets become time spans per stream. For video the span runs to the NEXT KEYFRAME (a damaged P/B frame
   breaks every picture until the next IDR), and when the damaged frame is a keyframe itself it starts at the
   previous keyframe. The union of the video and audio spans is cut to whole GOPs (the start moves down to the
   keyframe that opens the damaged picture's GOP, the end up to the next good keyframe), so all streams can be cut
   at the same times and every kept part starts on a keyframe: that is `DeepScanResult.spans`, what
   core/cut_damaged.py cuts out. A video-less file keeps the audio spans as they are.
"""

from __future__ import annotations

import bisect
import os
import re
import struct
import threading
from dataclasses import dataclass, field
from typing import Callable, Iterator, Optional

from redactor_common.core.subprocess_utils import popen_tool

from core.external_tools import get_executable_path

CHUNK_BYTES = 8 * 1024 * 1024
MIN_ZERO_RUN = 64 * 1024
# A packet is damaged when zeros cover more than this part of it (and at least _MIN_OVERLAP_BYTES): a packet that
# merely ENDS in a few legitimate zero bytes right next to a damaged block is not lost.
_OVERLAP_FRACTION = 0.05
_MIN_OVERLAP_BYTES = 16
_ZERO_HEAD_BYTES = 8
EDGE_BYTES = 8 * 1024 * 1024  # the ordinary check reads this much at each end of the media data
_TIME_EPSILON = 0.002


class DeepScanCancelled(Exception):
    pass


@dataclass
class ZeroRun:
    start: int          # byte offset (inclusive)
    end: int            # byte offset (exclusive)
    in_media: bool = True   # touches at least one packet (False: padding between packets, harmless)

    @property
    def length(self) -> int:
        return self.end - self.start


@dataclass
class DamagedSpan:
    start_s: float
    end_s: float

    @property
    def seconds(self) -> float:
        return self.end_s - self.start_s


@dataclass
class DeepScanResult:
    file_size: int = 0
    mtime_ns: int = 0                # of the file when it was scanned (a cut refuses a file changed since)
    container: str = ""              # "mp4", "mkv" or "other"
    approximate: bool = False        # MKV / other: the media-data range is a best guess
    duration: Optional[float] = None
    zero_runs: list[ZeroRun] = field(default_factory=list)
    spans: list[DamagedSpan] = field(default_factory=list)   # the union to cut out (video ends at keyframes)
    video_seconds: float = 0.0       # damaged video time (union of the video spans)
    audio_seconds: float = 0.0       # damaged audio time
    keyframes: list[tuple[float, float]] = field(default_factory=list)   # (pts, dts) of every video keyframe
    packets_checked: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def media_runs(self) -> list[ZeroRun]:
        return [r for r in self.zero_runs if r.in_media]

    @property
    def has_damage(self) -> bool:
        return bool(self.spans)

    @property
    def total_seconds(self) -> float:
        return sum(s.seconds for s in self.spans)

    @property
    def percent(self) -> float:
        return 100.0 * self.total_seconds / self.duration if self.duration else 0.0

    @property
    def audio_affected(self) -> bool:
        return self.audio_seconds > 0.0


# ---------------------------------------------------------------------------
# Where the media data is
# ---------------------------------------------------------------------------

def _mp4_media_ranges(path: str) -> Optional[list[tuple[int, int]]]:
    """The (start, end) byte ranges of the data inside every top-level mdat box (the box header is excluded), or
    None when the file doesn't look like an MP4 at all."""
    size = os.path.getsize(path)
    ranges: list[tuple[int, int]] = []
    seen_ftyp = False
    with open(path, "rb") as handle:
        position = 0
        while position + 8 <= size:
            handle.seek(position)
            header = handle.read(8)
            if len(header) < 8:
                break
            box_size, box_type = struct.unpack(">I4s", header)
            header_len = 8
            if box_size == 1:
                big = handle.read(8)
                if len(big) < 8:
                    break
                box_size = struct.unpack(">Q", big)[0]
                header_len = 16
            elif box_size == 0:
                box_size = size - position
            if box_type in (b"ftyp", b"moov", b"mdat", b"free", b"skip", b"wide", b"moof", b"styp", b"sidx", b"uuid",
                            b"pnot", b"meta", b"mfra"):
                seen_ftyp = True
            elif not seen_ftyp:
                return None
            if box_size < header_len:
                break
            if box_type == b"mdat":
                ranges.append((position + header_len, min(position + box_size, size)))
            position += box_size
    if not seen_ftyp:
        return None
    return ranges


def _mkv_media_range(path: str) -> Optional[tuple[int, int]]:
    """Approximate (start, end) of the Matroska clusters: the first Cluster up to the first Cues/Tags/Chapters/
    Attachments element after it, else to the end of the file. None when the file isn't Matroska."""
    from core import file_check as fc

    size = os.path.getsize(path)
    with open(path, "rb") as handle:
        if handle.read(4) != bytes.fromhex("1a45dfa3"):
            return None
        handle.seek(0)
        try:
            _id, _n, _o = fc._read_vint(handle, keep_marker=True)
            header_size, _n, _o = fc._read_vint(handle, keep_marker=False)
            handle.seek(header_size, os.SEEK_CUR)
            segment_id, _n, _o = fc._read_vint(handle, keep_marker=True)
            if segment_id != fc._EBML_SEGMENT:
                return (0, size)
            fc._read_vint(handle, keep_marker=False)
        except (ValueError, TypeError):
            return (0, size)
        first_cluster = None
        end = size
        after_media = {0x1C53BB6B, 0x1254C367, 0x1043A770, 0x1941A469}  # Cues, Tags, Chapters, Attachments
        for _ in range(500_000):
            element_start = handle.tell()
            try:
                element_id, _n, _o = fc._read_vint(handle, keep_marker=True)
                if element_id is None:
                    break
                element_size, _n, unknown = fc._read_vint(handle, keep_marker=False)
            except ValueError:
                # An element header that is not valid: if the clusters began, the rest is damage or media.
                break
            if element_size is None:
                break
            if element_id == fc._EBML_CLUSTER and first_cluster is None:
                first_cluster = element_start
            elif element_id in after_media and first_cluster is not None:
                end = element_start
                break
            if unknown:
                break
            handle.seek(handle.tell() + element_size)
            if handle.tell() >= size:
                break
        if first_cluster is None:
            return (0, size)
        return (first_cluster, end)


def media_ranges(path: str) -> tuple[list[tuple[int, int]], str, bool]:
    """(ranges to scan for zeros, container name, approximate)."""
    size = os.path.getsize(path)
    suffix = os.path.splitext(path)[1].lower()
    try:
        if suffix in (".mp4", ".m4v", ".mov", ".m4a"):
            found = _mp4_media_ranges(path)
            if found is not None:
                return found, "mp4", False
        if suffix in (".mkv", ".webm", ".mka"):
            found = _mkv_media_range(path)
            if found is not None:
                return [found], "mkv", True
        # Sniff the content when the extension lies.
        mp4 = _mp4_media_ranges(path)
        if mp4 is not None:
            return mp4, "mp4", False
        mkv = _mkv_media_range(path)
        if mkv is not None:
            return [mkv], "mkv", True
    except (OSError, struct.error):
        pass
    return [(0, size)], "other", True


# ---------------------------------------------------------------------------
# Zero runs, one streaming pass
# ---------------------------------------------------------------------------

def find_zero_runs(
    path: str,
    ranges: list[tuple[int, int]],
    min_run: int = MIN_ZERO_RUN,
    progress: Optional[Callable[[int, int], None]] = None,
    cancel: Optional[threading.Event] = None,
    chunk_bytes: int = CHUNK_BYTES,
    progress_base: int = 0,
    progress_total: Optional[int] = None,
) -> list[tuple[int, int]]:
    """Runs of at least `min_run` zero bytes inside `ranges` (byte-exact start/end), found in one streaming pass.
    `progress(done, total)` counts the bytes of the ranges (offset by `progress_base`); `cancel` is a threading.Event
    checked between chunks: raises DeepScanCancelled."""
    total = progress_total if progress_total is not None else sum(e - s for s, e in ranges)
    pattern = re.compile(b"\x00{%d,}" % min_run)
    runs: list[tuple[int, int]] = []
    done = progress_base
    with open(path, "rb") as handle:
        for start, end in ranges:
            handle.seek(start)
            position = start
            open_start: Optional[int] = None
            while position < end:
                if cancel is not None and cancel.is_set():
                    raise DeepScanCancelled()
                data = handle.read(min(chunk_bytes, end - position))
                if not data:
                    break
                n = len(data)
                lead = n - len(data.lstrip(b"\x00"))
                if lead == n:
                    if open_start is None:
                        open_start = position
                else:
                    if lead:
                        # The zeros at the start of this chunk end a run that began in an earlier chunk, or begin one
                        # right here (at the start of the range).
                        run_start = open_start if open_start is not None else position
                        if position + lead - run_start >= min_run:
                            runs.append((run_start, position + lead))
                    open_start = None
                    trail = n - len(data.rstrip(b"\x00"))
                    middle = data[lead:n - trail] if trail else data[lead:]
                    if middle.count(0) >= min_run:
                        for match in pattern.finditer(middle):
                            runs.append((position + lead + match.start(), position + lead + match.end()))
                    if trail:
                        open_start = position + n - trail
                position += n
                done += n
                if progress is not None:
                    progress(done, total)
            if open_start is not None and position - open_start >= min_run:
                runs.append((open_start, position))
    return runs


# ---------------------------------------------------------------------------
# Packets
# ---------------------------------------------------------------------------

@dataclass
class _Packet:
    stream: int
    pos: int
    size: int
    pts: Optional[float]
    dts: Optional[float]
    duration: float
    key: bool
    damaged: bool = False

    @property
    def start(self) -> Optional[float]:
        """The earliest time the packet matters at: the decode time (it is never later than the presentation time)."""
        times = [t for t in (self.dts, self.pts) if t is not None]
        return min(times) if times else None


def _float(value: str) -> Optional[float]:
    try:
        return float(value)
    except ValueError:
        return None


def _stream_kinds(path: str, run) -> dict[int, str]:
    """{stream index: "video"/"audio"} for the real video and audio streams (cover pictures excluded)."""
    import json

    done = run(["ffprobe", "-v", "error", "-show_entries", "stream=index,codec_type,codec_name:stream_disposition=attached_pic",
                "-of", "json", path])
    kinds: dict[int, str] = {}
    if done is None or done.returncode != 0:
        return kinds
    try:
        info = json.loads(done.stdout or "{}")
    except json.JSONDecodeError:
        return kinds
    for stream in info.get("streams") or []:
        kind = stream.get("codec_type")
        if kind not in ("video", "audio"):
            continue
        if (stream.get("disposition") or {}).get("attached_pic"):
            continue
        kinds[int(stream["index"])] = kind
    return kinds


def _iter_packets(path: str, cancel: Optional[threading.Event]) -> Iterator[_Packet]:
    """ffprobe's packet listing, streamed (a two-hour file has a few hundred thousand lines)."""
    import subprocess

    args = [get_executable_path("ffprobe"), "-v", "quiet", "-show_entries",
            "packet=stream_index,pts_time,dts_time,duration_time,pos,size,flags", "-of", "compact=p=0", path]
    proc = popen_tool(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            if cancel is not None and cancel.is_set():
                raise DeepScanCancelled()
            fields = dict(part.split("=", 1) for part in line.strip().split("|") if "=" in part)
            if "stream_index" not in fields or fields.get("pos", "N/A") == "N/A":
                continue
            try:
                yield _Packet(
                    stream=int(fields["stream_index"]), pos=int(fields["pos"]), size=int(fields.get("size", "0")),
                    pts=_float(fields.get("pts_time", "N/A")), dts=_float(fields.get("dts_time", "N/A")),
                    duration=_float(fields.get("duration_time", "N/A")) or 0.0,
                    key="K" in fields.get("flags", ""),
                )
            except ValueError:
                continue
    finally:
        if proc.poll() is None:
            proc.kill()
        try:
            proc.communicate(timeout=5)
        except Exception:  # noqa: BLE001 -- the process is dead or being reaped; nothing to report
            pass


def _nal_walk_ok(data: bytes, length_size: int = 4) -> bool:
    """True when `data` is a sequence of NAL units each preceded by a `length_size`-byte length that adds up exactly."""
    position = 0
    n = len(data)
    if n < length_size + 1:
        return False
    while position < n:
        if position + length_size > n:
            return False
        length = int.from_bytes(data[position:position + length_size], "big")
        position += length_size
        if length == 0 or position + length > n:
            return False
        position += length
    return position == n


def _mark_damaged(
    packets: list[_Packet], kinds: dict[int, str], runs: list[ZeroRun], handle,
) -> None:
    """Sets `damaged` on the packets a zero run hits and flags the runs that hit any packet (`in_media`)."""
    by_pos = sorted(packets, key=lambda p: p.pos)
    positions = [p.pos for p in by_pos]
    for run in runs:
        first = max(bisect.bisect_right(positions, run.start) - 1, 0)
        index = first
        while index < len(by_pos) and by_pos[index].pos < run.end:
            packet = by_pos[index]
            index += 1
            overlap = min(packet.pos + packet.size, run.end) - max(packet.pos, run.start)
            if overlap <= 0 or packet.size <= 0:
                continue
            run.in_media = True
            if overlap >= _MIN_OVERLAP_BYTES and overlap > packet.size * _OVERLAP_FRACTION:
                packet.damaged = True
                continue
            # A small overlap: look at the packet itself.
            handle.seek(packet.pos)
            data = handle.read(packet.size)
            if len(data) < packet.size or not any(data[:_ZERO_HEAD_BYTES]):
                packet.damaged = True
            elif kinds.get(packet.stream) == "video" and data[:3] != b"\x00\x00\x01" and data[:4] != b"\x00\x00\x00\x01":
                if not _nal_walk_ok(data):
                    packet.damaged = True


def _merge(spans: list[tuple[float, float]], gap: float = _TIME_EPSILON) -> list[tuple[float, float]]:
    spans = sorted(spans)
    merged: list[tuple[float, float]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1] + gap:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _video_spans(video: list[_Packet], end_of_file: float) -> list[tuple[float, float]]:
    """Damaged video time: from the first broken picture to the next good keyframe (decode order), and from the
    previous keyframe when the damaged picture is a keyframe itself."""
    spans: list[tuple[float, float]] = []
    broken_start: Optional[float] = None
    previous_key: Optional[float] = None
    for packet in video:
        start = packet.start
        if start is None:
            continue
        if packet.key and not packet.damaged:
            if broken_start is not None:
                # The span ends at the keyframe's PRESENTATION time: that is where a cut resumes cleanly (seeking to
                # its decode time can land on the keyframe before it).
                spans.append((broken_start, packet.pts if packet.pts is not None else start))
                broken_start = None
            previous_key = start
            continue
        if packet.damaged:
            begin = previous_key if (packet.key and previous_key is not None) else start
            broken_start = begin if broken_start is None else min(broken_start, begin)
        elif broken_start is not None:
            broken_start = min(broken_start, start)
    if broken_start is not None:
        spans.append((broken_start, end_of_file))
    return spans


def _total(spans: list[tuple[float, float]]) -> float:
    return sum(end - start for start, end in spans)


def analyse_packets(
    path: str,
    zero_runs: list[ZeroRun],
    duration: Optional[float],
    run,
    cancel: Optional[threading.Event] = None,
) -> tuple[list[DamagedSpan], float, float, list[tuple[float, float]]]:
    """(cut spans, damaged video seconds, damaged audio seconds, every video keyframe as (pts, dts)). Marks
    `in_media` on the zero runs."""
    kinds = _stream_kinds(path, run)
    packets = [p for p in _iter_packets(path, cancel) if p.stream in kinds]
    with open(path, "rb") as handle:
        _mark_damaged(packets, kinds, zero_runs, handle)
    ends = [p.start + p.duration for p in packets if p.start is not None]
    end_of_file = max([duration or 0.0] + ends)
    video_stream = next((i for i, k in kinds.items() if k == "video"), None)
    video = [p for p in packets if p.stream == video_stream]
    audio_spans: list[tuple[float, float]] = []
    for packet in packets:
        if kinds[packet.stream] == "audio" and packet.damaged and packet.start is not None:
            audio_spans.append((packet.start, packet.start + max(packet.duration, 0.0)))
    video_spans = _video_spans(video, end_of_file)
    video_merged = _merge(video_spans)
    audio_merged = _merge(audio_spans, gap=0.05)
    all_keys = sorted(
        ((p.pts if p.pts is not None else p.start), p.start) for p in video if p.key and p.start is not None
    )  # (presentation time, decode time) of every video keyframe, damaged or not
    good_key_pts = sorted(
        (p.pts if p.pts is not None else p.start) for p in video if p.key and not p.damaged and p.start is not None
    )
    key_dts = [dts for _pts, dts in all_keys]
    # Every stream is cut at the same times and the picture can only start again at a keyframe, so a cut is a whole
    # number of GOPs: each span starts at the keyframe that opens the damaged picture's GOP (the pictures before it
    # in that GOP can't be kept cleanly) and ends at the next good keyframe.
    snapped: list[tuple[float, float]] = []
    for start, end in _merge(video_merged + audio_merged):
        if video_stream is not None:
            index = bisect.bisect_right(key_dts, start + _TIME_EPSILON) - 1
            if index >= 0:
                start = all_keys[index][0]
            index = bisect.bisect_left(good_key_pts, end - _TIME_EPSILON)
            end = good_key_pts[index] if index < len(good_key_pts) else max(end, end_of_file)
        snapped.append((start, end))
    spans = [DamagedSpan(max(s, 0.0), e) for s, e in _merge(snapped)]
    return spans, _total(video_merged), _total(audio_merged), all_keys


# ---------------------------------------------------------------------------
# The scan
# ---------------------------------------------------------------------------

def deep_scan(
    path: str,
    run=None,
    progress: Optional[Callable[[int, int], None]] = None,
    cancel: Optional[threading.Event] = None,
    min_run: int = MIN_ZERO_RUN,
    chunk_bytes: int = CHUNK_BYTES,
    duration: Optional[float] = None,
    progress_base: int = 0,
    progress_total: Optional[int] = None,
) -> DeepScanResult:
    """Reads `path` once and returns the damage found (see the module docstring). Raises DeepScanCancelled when
    `cancel` is set. `run` is ffmpeg_backend's tool runner (default: the app's own)."""
    if run is None:
        from core.ffmpeg_backend import _run as run
    ranges, container, approximate = media_ranges(path)
    stat = os.stat(path)
    result = DeepScanResult(file_size=stat.st_size, mtime_ns=stat.st_mtime_ns, container=container, approximate=approximate)
    raw = find_zero_runs(path, ranges, min_run, progress, cancel, chunk_bytes, progress_base, progress_total)
    result.zero_runs = [ZeroRun(s, e, in_media=False) for s, e in raw]
    if duration is None:
        from core.ffmpeg_backend import get_duration_seconds

        duration = get_duration_seconds(path)
    result.duration = duration
    if not raw:
        return result
    spans, video_seconds, audio_seconds, keyframes = analyse_packets(path, result.zero_runs, duration, run, cancel)
    result.packets_checked = True
    result.spans = spans
    result.keyframes = keyframes
    result.video_seconds = video_seconds
    result.audio_seconds = audio_seconds
    if approximate:
        result.notes.append("The media data of this container type was located approximately")
    return result


def edge_zero_blocks(path: str, edge_bytes: Optional[int] = None, min_run: int = MIN_ZERO_RUN) -> int:
    """The cheap check the ordinary Check Files always does: how many zero runs of at least `min_run` lie in the
    first and last `edge_bytes` of the media data (needs no ffprobe, reads at most 2 x edge_bytes)."""
    edge_bytes = EDGE_BYTES if edge_bytes is None else edge_bytes
    ranges, _container, _approx = media_ranges(path)
    edges: list[tuple[int, int]] = []
    for start, end in ranges:
        if end - start <= 2 * edge_bytes:
            edges.append((start, end))
        else:
            edges.append((start, start + edge_bytes))
            edges.append((end - edge_bytes, end))
    return len(find_zero_runs(path, edges, min_run))
