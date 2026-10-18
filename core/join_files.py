"""
core/join_files.py

Join the parts of one movie (a file split over several: "Title cd1.mp4", "Title cd2.mp4", also disc / part / pt) into
ONE file, losslessly (ffmpeg's concat demuxer with stream copy: nothing is re-encoded). The same safety rules as the
container remux (core/container_remux.py), all through core/library_safety.SafeRun:

  * only parts that really can be joined without re-encoding are joined: same container, and the same video codec,
    size, pixel format and frame rate, the same audio tracks (codec, channels, sample rate) and the same subtitle
    codecs. A group that differs is shown with the reason and left alone;
  * the joined file is written to a temp name beside the first part, verified (same tracks, length = the sum of the
    parts within the Library safety tolerance per part, a FULL decode with zero errors, the poster and the tags
    read back) and only then swapped in;
  * the tags and the poster come from the FIRST part (the Scene # is cleared: the result is the whole movie);
  * never deletes media: every part is MOVED to a "joined originals YYYY-MM-DD" folder (Library safety's quarantine
    folder, else a folder beside the file) and a UTF-8 TSV log (the remux log's columns) lets undo_join_log() put
    everything back;
  * a dry run lists what would be joined and writes nothing; a part modified in the last N minutes is skipped; an
    existing target is never overwritten.

Optionally one chapter per part ("Part 1", "Part 2", ...) is added at the joins.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from typing import Callable

from core import conversion_run, history, library_safety
from core import container_remux
from core.config import get_setting
from core.container_remux import RemuxLog
from core.ffmpeg_backend import _run, _run_ffmpeg, probe_media
from core.library_safety import KIND_JOINED, OUTPUT_EXTENSIONS, SafetyError
from redactor_common.gui.sortable_table import natural_key

JOINED = "joined"
WOULD = "would join"
SKIPPED = "skipped"
FAILED = "failed"
UNDONE_FOLDER = "undone join"

# "Title cd1", "Title - CD 2", "Title (disc1)", "Title [part 2]", "Title pt1", "Title cd1 of 2": a part marker at the END
# of the name. Group 1 is the movie's name, group 2 the part number.
_PART_TAIL = re.compile(
    r"^(?P<base>.*?)[\s._\-]*[\(\[]?\s*(?:cd|disc|disk|part|pt)[\s._-]*0*(?P<n>\d{1,2})(?:\s*of\s*\d+)?\s*[\)\]]?\s*$",
    re.IGNORECASE,
)


def split_part_name(stem: str) -> tuple[str, int | None]:
    """('Strap Some Boyz 2', 1) for 'Strap Some Boyz 2 cd1'; (stem, None) when the name has no part marker (or nothing
    but the marker: 'cd1')."""
    match = _PART_TAIL.match(stem or "")
    if not match or not match.group("base").strip():
        return stem, None
    return match.group("base").strip(), int(match.group("n"))


@dataclass
class JoinGroup:
    """The parts that make one movie, in the order they are joined."""
    name: str                  # the movie's name = the joined file's stem
    folder: str
    parts: list[str]
    numbers: list[int | None]
    note: str = ""             # why the order is a guess / the group cannot be joined, "" when fine

    @property
    def extension(self) -> str:
        return os.path.splitext(self.parts[0])[1].lower() if self.parts else ""

    @property
    def output_path(self) -> str:
        return os.path.join(self.folder, self.name + self.extension)


def _common_start(texts: list[str]) -> str:
    """The longest start all of `texts` share (case kept from the first)."""
    first = texts[0]
    length = 0
    while length < len(first) and all(len(t) > length and t[length].casefold() == first[length].casefold() for t in texts):
        length += 1
    return first[:length]


def group_files(paths: list[str]) -> list[JoinGroup]:
    """Groups `paths` into movies: files in one folder whose names are the same apart from a trailing part marker. Parts
    are ordered by their number. A selection with no part markers at all is ONE group in natural name order (named by
    the common start of the names), so two files picked by hand can still be joined. A single file with a marker or a
    group with missing or repeated numbers is returned with a `note`; the caller shows it and does not join it."""
    unique = list(dict.fromkeys(os.path.abspath(p) for p in paths))
    marked: dict[tuple[str, str], list[tuple[int, str]]] = {}
    unmarked: list[str] = []
    for path in unique:
        stem = os.path.splitext(os.path.basename(path))[0]
        base, number = split_part_name(stem)
        if number is None:
            unmarked.append(path)
        else:
            marked.setdefault((os.path.dirname(path), base.casefold()), []).append((number, path))
    groups: list[JoinGroup] = []
    for (folder, _key), items in marked.items():
        items.sort(key=lambda item: (item[0], natural_key(os.path.basename(item[1]))))
        name = split_part_name(os.path.splitext(os.path.basename(items[0][1]))[0])[0]
        numbers = [n for n, _p in items]
        group = JoinGroup(name, folder, [p for _n, p in items], numbers)
        if len(items) < 2:
            group.note = "only one part was selected"
        elif len(set(numbers)) != len(numbers):
            group.note = "two files have the same part number"
        elif numbers != list(range(numbers[0], numbers[0] + len(numbers))):
            missing = sorted(set(range(numbers[0], numbers[-1] + 1)) - set(numbers))
            group.note = "part " + ", ".join(str(m) for m in missing) + " is missing from the selection"
        groups.append(group)
    if unmarked and not marked and len(unmarked) >= 2:
        by_folder: dict[str, list[str]] = {}
        for path in unmarked:
            by_folder.setdefault(os.path.dirname(path), []).append(path)
        for folder, files in by_folder.items():
            files.sort(key=lambda p: natural_key(os.path.basename(p)))
            stems = [os.path.splitext(os.path.basename(p))[0] for p in files]
            common = _common_start(stems).rstrip(" ._-([")
            name = common if len(common) >= 3 else stems[0] + " (joined)"
            group = JoinGroup(name, folder, files, [None] * len(files),
                              note="" if len(files) >= 2 else "only one file was selected")
            groups.append(group)
    elif unmarked:
        for path in unmarked:  # mixed selection: the unmarked files cannot be placed in any movie
            groups.append(JoinGroup(os.path.splitext(os.path.basename(path))[0], os.path.dirname(path), [path], [None],
                                    note="no part marker (cd1, part 2, ...) in the name"))
    groups.sort(key=lambda g: natural_key(g.output_path))
    return groups


# --- compatibility -----------------------------------------------------------------------------------------------------


def stream_signature(path: str) -> list[tuple] | None:
    """What must be identical for a stream copy join: per stream (cover pictures left out) its kind and codec and, for
    video, size / pixel format / frame rate, for audio channels / sample rate. None when ffprobe could not read it."""
    result = _run(["ffprobe", "-v", "error", "-show_entries",
                   "stream=codec_type,codec_name,profile,width,height,pix_fmt,r_frame_rate,sample_rate,channels:"
                   "stream_disposition=attached_pic", "-of", "json", path])
    if result is None or result.returncode != 0:
        return None
    try:
        streams = json.loads(result.stdout).get("streams") or []
    except (ValueError, TypeError, AttributeError):
        return None
    signature = []
    for s in streams:
        if not isinstance(s, dict) or (s.get("disposition") or {}).get("attached_pic"):
            continue
        kind = s.get("codec_type")
        if kind == "video":
            signature.append(("video", s.get("codec_name"), s.get("width"), s.get("height"), s.get("pix_fmt"),
                              s.get("r_frame_rate")))
        elif kind == "audio":
            signature.append(("audio", s.get("codec_name"), s.get("channels"), s.get("sample_rate")))
        elif kind == "subtitle":
            signature.append(("subtitle", s.get("codec_name")))
    return signature


def _describe_difference(first: list[tuple], other: list[tuple]) -> str:
    if len(first) != len(other):
        return f"{len(other)} tracks against {len(first)}"
    for a, b in zip(first, other):
        if a != b:
            if a[0] == "video":
                return f"video {a[1]} {a[2]}x{a[3]} {a[5]} fps against {b[1]} {b[2]}x{b[3]} {b[5]} fps"
            return f"{a[0]} {a[1:]} against {b[1:]}"
    return "different"


@dataclass
class JoinPlan:
    group: JoinGroup
    compatible: bool = False
    reason: str = ""                       # why it cannot be joined ("" when it can)
    durations: list[float | None] = field(default_factory=list)
    summary: str = ""                      # e.g. "H.264 1920x1080 25 fps, AAC"

    @property
    def total_seconds(self) -> float | None:
        return sum(self.durations) if self.durations and all(d is not None for d in self.durations) else None


def check_group(
    group: JoinGroup, *, signature: Callable[[str], list[tuple] | None] = stream_signature,
    probe: Callable[[str], object] = probe_media,
) -> JoinPlan:
    """Whether `group` can be joined without re-encoding, and why not."""
    plan = JoinPlan(group)
    if group.note:
        plan.reason = group.note
        return plan
    if group.extension not in OUTPUT_EXTENSIONS:
        plan.reason = f"only MP4 and MKV files are joined (these are {group.extension or 'without an extension'})"
        return plan
    if any(os.path.splitext(p)[1].lower() != group.extension for p in group.parts):
        plan.reason = "the parts are not all the same kind of file"
        return plan
    signatures = []
    for path in group.parts:
        sig = signature(path)
        if sig is None:
            plan.reason = f"{os.path.basename(path)} could not be read (ffprobe failed)"
            return plan
        signatures.append(sig)
        info = probe(path)
        plan.durations.append(getattr(info, "duration", None) if info is not None else None)
    for path, sig in zip(group.parts[1:], signatures[1:]):
        if sig != signatures[0]:
            plan.reason = (f"{os.path.basename(path)} differs from {os.path.basename(group.parts[0])}: "
                           f"{_describe_difference(signatures[0], sig)}; joining would need a re-encode")
            return plan
    if any(d is None for d in plan.durations):
        plan.reason = "a part has no readable length"
        return plan
    if os.path.lexists(group.output_path) and os.path.normcase(group.output_path) not in {
            os.path.normcase(p) for p in group.parts}:
        plan.reason = f"{os.path.basename(group.output_path)} already exists (never overwritten)"
        return plan
    first = signatures[0]
    video = next((s for s in first if s[0] == "video"), None)
    audio = next((s for s in first if s[0] == "audio"), None)
    bits = []
    if video:
        bits.append(f"{video[1]} {video[2]}x{video[3]} {video[5]} fps")
    if audio:
        bits.append(f"{audio[1]} {audio[2]} ch")
    plan.summary = ", ".join(str(b) for b in bits)
    plan.compatible = True
    return plan


# --- the join ----------------------------------------------------------------------------------------------------------


@dataclass
class JoinResult:
    name: str
    status: str                       # JOINED / WOULD / SKIPPED / FAILED
    message: str = ""
    new_path: str = ""
    quarantined: list[str] = field(default_factory=list)

    def line(self) -> str:
        head = f"{self.status.upper()}: {self.name}"
        return f"{head} -> {os.path.basename(self.new_path)} ({self.message})" if self.new_path else f"{head} ({self.message})"


def _concat_quote(path: str) -> str:
    """One `file '...'` line of the concat demuxer's list: single quotes escaped the demuxer's way."""
    return "file '" + path.replace("\\", "/").replace("'", "'\\''") + "'"


def _chapters_text(durations: list[float]) -> str:
    lines = [";FFMETADATA1"]
    start = 0.0
    for index, length in enumerate(durations, 1):
        end = start + length
        lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={int(start * 1000)}", f"END={int(end * 1000)}", f"title=Part {index}"]
        start = end
    return "\n".join(lines) + "\n"


def join_group(
    plan: JoinPlan,
    *,
    dry_run: bool = True,
    chapters: bool = False,
    safety: library_safety.SafetySettings | None = None,
    log: RemuxLog | None = None,
    cancel_event=None,
    library_root: str | None = None,
    clock: Callable[[], float] | None = None,
    probe: Callable[[str], object] = probe_media,
    runner: Callable = _run_ffmpeg,
    decode_check: Callable[[str], str] = conversion_run.decode_problem,
) -> JoinResult:
    """Joins one checked group (or reports what would be done). Never raises for an ordinary failure: the result says
    what happened, and every part is untouched unless the status is JOINED."""
    group = plan.group
    name = os.path.basename(group.output_path)
    if not plan.compatible:
        return JoinResult(name, SKIPPED, plan.reason or "cannot be joined without a re-encode")
    safety = safety or library_safety.get_safety_settings()
    if library_root is None:
        library_root = get_setting("rename", "library_root", "").strip()
    capture = container_remux._CaptureLog()  # noqa: SLF001 -- the same quarantine bookkeeping as the remux
    run = container_remux._make_run(group.parts[0], safety, dry_run, capture, clock, library_root)  # noqa: SLF001
    for path in group.parts:
        if not os.path.isfile(path):
            return JoinResult(name, FAILED, f"{os.path.basename(path)} is not there")
        busy = run.busy_reason(path)
        if busy:
            return JoinResult(name, SKIPPED, f"{os.path.basename(path)}: {busy}")
    total = plan.total_seconds or 0.0
    if dry_run:
        return JoinResult(name, WOULD, f"{len(group.parts)} parts, about {total / 60:.0f} min, lossless", group.output_path)

    first = group.parts[0]
    info0 = probe(first)
    if info0 is None:
        return JoinResult(name, FAILED, "couldn't read the first part (ffprobe failed)")
    cover, cover_problem = conversion_run.read_source_cover(first, info0)
    if cover_problem:
        return JoinResult(name, FAILED, cover_problem)
    tags = conversion_run.read_source_tags(first)
    if tags is not None:
        tags.episode_number = None  # the result is the whole movie, not one scene or part
    sizes = {p: os.path.getsize(p) for p in group.parts}
    final = group.output_path
    is_mp4 = group.extension in (".mp4", ".m4v")

    def build(_source: str, temp: str) -> tuple[bool, str]:
        folder = os.path.dirname(os.path.abspath(final))
        list_path = meta_path = ""
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".txt", dir=folder, delete=False, encoding="utf-8",
                                             prefix="redact-join-") as handle:
                handle.write("\n".join(_concat_quote(p) for p in group.parts) + "\n")
                list_path = handle.name
            args = ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", list_path]
            if chapters:
                with tempfile.NamedTemporaryFile("w", suffix=".txt", dir=folder, delete=False, encoding="utf-8",
                                                 prefix="redact-join-meta-") as handle:
                    handle.write(_chapters_text([d or 0.0 for d in plan.durations]))
                    meta_path = handle.name
                args += ["-f", "ffmetadata", "-i", meta_path, "-map_chapters", "1"]
            args += ["-map", "0:V", "-map", "0:a?", "-map", "0:s?", "-c", "copy", "-map_metadata", "0"]
            if is_mp4:
                args += ["-movflags", "+faststart"]
            args.append(temp)
            code, stderr = runner(args, cancel_event=cancel_event, timeout=conversion_run.CONVERT_TIMEOUT_SECONDS)
            if code != 0:
                lines = (stderr or "").strip().splitlines()
                return False, lines[-1] if lines else "ffmpeg failed"
        finally:
            for leftover in (list_path, meta_path):
                if leftover:
                    try:
                        os.remove(leftover)
                    except OSError:
                        pass
        if cover:
            problem = conversion_run.restore_cover(temp, cover)
            if problem:
                return False, f"the poster could not be carried over ({problem})"
        if tags is not None:
            problem = conversion_run.write_tags(temp, tags)
            if problem:
                return False, f"the tags could not be written ({problem})"
        return True, ""

    def verify(_source: str, temp: str) -> str:
        result = probe(temp)
        if result is None:
            return "couldn't read the result back (ffprobe failed)"
        problems = []
        for kind in ("video", "audio", "subtitle"):
            if len(result.of(kind)) != len(info0.of(kind)):
                problems.append(f"{kind} tracks: {len(info0.of(kind))} before, {len(result.of(kind))} after")
        allowed = max(run.tolerance_seconds * len(group.parts), total * run.tolerance_fraction)
        if result.duration is None or abs(result.duration - total) > allowed:
            problems.append(f"length {result.duration if result.duration is None else round(result.duration, 1)} s "
                            f"against the parts' {total:.1f} s")
        if not problems:
            problem = decode_check(temp)
            if problem:
                problems.append(problem)
        if not problems and cover:
            problem = conversion_run.verify_cover(temp, cover)
            if problem:
                problems.append(problem)
        return ", ".join(problems)

    reason = f"join {len(group.parts)} parts"
    try:
        ok = run.convert(first, final, build, KIND_JOINED, reason, verify=verify)
    except SafetyError as exc:
        return JoinResult(name, FAILED, f"the parts are kept: {exc}")
    if not ok:
        detail = run.actions[-1].detail if run.actions else "not joined"
        return JoinResult(name, SKIPPED if "left alone" in detail else FAILED, detail)
    quarantined = {first: next((new for new, old in capture.moves if old == first), "")}
    notes = []
    for path in group.parts[1:]:
        dest = run.quarantine(path, KIND_JOINED, reason)
        if dest is None:
            notes.append(f"{os.path.basename(path)} could not be moved and is still beside the joined file")
        quarantined[path] = next((new for new, old in capture.moves if old == path), dest or "")
    if os.path.normcase(os.path.abspath(final)) != os.path.normcase(os.path.abspath(first)):
        history.record_move(first, final)
    new_bytes = os.path.getsize(final) if os.path.isfile(final) else 0
    if log is not None:
        for path in group.parts:
            if quarantined.get(path):
                log.append(path, quarantined[path], final, sizes[path], new_bytes, JOINED)
    where = ", ".join(sorted({os.path.dirname(q) for q in quarantined.values() if q and q != "Recycle Bin"}))
    message = f"{len(group.parts)} parts joined; originals in {where or 'the Recycle Bin'}"
    if notes:
        message += "; " + "; ".join(notes)
    return JoinResult(name, JOINED, message, final, [q for q in quarantined.values() if q])


def default_log_path(first_path: str, safety: library_safety.SafetySettings) -> str:
    """The remux log's rules, with a 'join log' name."""
    path = container_remux.default_log_path(first_path, safety)
    return os.path.join(os.path.dirname(path), os.path.basename(path).replace("remux log", "join log", 1))


def join_groups(
    groups: list[JoinGroup], *, dry_run: bool = True, chapters: bool = False,
    safety: library_safety.SafetySettings | None = None, cancel_event=None, log_path: str = "",
    on_result: Callable[[JoinResult], None] | None = None, plans: list[JoinPlan] | None = None, **kwargs,
) -> tuple[list[JoinResult], str]:
    """check_group() + join_group() for each group (stops between groups when `cancel_event` is set). `plans` are
    reused when the caller already checked them. Returns (results, the TSV's path or "" for a dry run)."""
    safety = safety or library_safety.get_safety_settings()
    plans = plans or [check_group(g) for g in groups]
    log = None
    if not dry_run and plans:
        log = RemuxLog(log_path or default_log_path(plans[0].group.parts[0], safety))
    results: list[JoinResult] = []
    for plan in plans:
        if cancel_event is not None and cancel_event.is_set():
            break
        result = join_group(plan, dry_run=dry_run, chapters=chapters, safety=safety, log=log,
                            cancel_event=cancel_event, **kwargs)
        results.append(result)
        if on_result is not None:
            on_result(result)
    return results, (log.path if log is not None and os.path.isfile(log.path) else "")


def undo_join_log(log_path: str, day=None) -> tuple[list[str], list[str]]:
    """Puts back every part recorded in a log written by join_groups(): the joined file is moved (never deleted) into
    'undone join <date>' beside it, each quarantined part goes back to its old path. Returns (status lines, problems)."""
    return container_remux.undo_remux_log(log_path, day, done_status=JOINED, aside_folder=UNDONE_FOLDER)
