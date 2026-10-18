"""
core/container_remux.py

The fix for a file whose container does not match its extension (core/container_check.py): a LOSSLESS remux into
the container its name says, through the same verified path as every library conversion (core/conversion_run.py
convert_file + core/library_safety.SafeRun). The owner's rules, all enforced here:

  * never deletes media: the original is MOVED to a quarantine folder, "remuxed originals YYYY-MM-DD" -- the
    folder set in Preferences > Library safety when there is one, else a folder of that name beside the file;
  * a UTF-8 TSV log, one row per file (timestamp, original, quarantined, new, sizes, result), which
    undo_remux_log() reads to put everything back;
  * converts to a temp name beside the file, verifies it (container is now really MP4/Matroska, duration, same
    audio/video/subtitle tracks, a FULL decode with zero errors) and only then swaps;
  * a dry run lists what would be remuxed and writes nothing;
  * a file modified in the last N minutes (default 10) is skipped and reported;
  * an existing target is never overwritten; a file that would need a re-encode is skipped, never re-encoded.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable

from core import conversion_plan as cp
from core import conversion_run, library_safety
from core.config import get_setting
from core.ffmpeg_backend import _run_ffmpeg, probe_media
from core.library_safety import SafeRun, SafetyError, UndoLog

LOG_COLUMNS = ("timestamp", "original", "quarantined", "new", "original_bytes", "new_bytes", "result")
UNDONE_FOLDER = "undone remux"

REMUXED = "remuxed"
WOULD = "would remux"
SKIPPED = "skipped"
FAILED = "failed"
RIGHT = "container is right"
UNDONE = "undone"


@dataclass
class RemuxResult:
    path: str
    status: str  # REMUXED / WOULD / SKIPPED / FAILED / RIGHT
    message: str = ""
    new_path: str = ""
    quarantined: str = ""

    def line(self) -> str:
        head = f"{self.status.upper()}: {os.path.basename(self.path)}"
        return f"{head} -> {os.path.basename(self.new_path)} ({self.message})" if self.new_path else f"{head} ({self.message})"


class _CaptureLog(UndoLog):
    """Records SafeRun's moves in memory (the TSV of this feature has its own columns)."""

    def __init__(self) -> None:
        super().__init__("")
        self.moves: list[tuple[str, str]] = []

    def moved(self, new_path: str, old_path: str) -> None:
        self._check(new_path, old_path)
        self.moves.append((new_path, old_path))

    def created(self, path: str) -> None:
        self._check(path)


class RemuxLog:
    """The TSV log, UTF-8, header first, each row flushed and fsynced as it is written."""

    def __init__(self, path: str):
        self.path = path

    def append(self, original: str, quarantined: str, new: str, original_bytes: int, new_bytes: int, result: str) -> None:
        cells = [datetime.now().strftime("%Y-%m-%d %H:%M:%S"), original, quarantined, new, str(original_bytes), str(new_bytes), result]
        for cell in cells:
            if any(c in cell for c in "\t\r\n"):
                raise SafetyError(f"a tab or line break can't be logged: {cell!r}")
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        fresh = not os.path.isfile(self.path)
        with open(self.path, "a", encoding="utf-8", newline="\n") as f:
            if fresh:
                f.write("\t".join(LOG_COLUMNS) + "\n")
            f.write("\t".join(cells) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def rows(self) -> list[dict[str, str]]:
        if not os.path.isfile(self.path):
            return []
        with open(self.path, encoding="utf-8", newline="") as f:
            lines = [raw for raw in f.read().split("\n") if raw.strip()]
        out = []
        for raw in lines:
            cells = raw.split("\t")
            if cells[0] == LOG_COLUMNS[0] or len(cells) != len(LOG_COLUMNS):
                continue
            out.append(dict(zip(LOG_COLUMNS, cells)))
        return out


def default_log_path(first_path: str, safety: library_safety.SafetySettings, day: date | None = None) -> str:
    """Where a run's TSV goes: Library safety's undo log folder, else beside the quarantine folder, else inside the
    dated quarantine folder beside the first file."""
    stamp = library_safety.stamp_now()
    name = f"remux log {stamp}.tsv"
    if safety.undo_log_folder:
        return os.path.join(safety.undo_log_folder, name)
    if safety.quarantine_root:
        return os.path.join(safety.quarantine_root, "_undo logs", name)
    folder = f"{library_safety.KIND_REMUXED} {(day or date.today()).isoformat()}"
    return os.path.join(os.path.dirname(os.path.abspath(first_path)), folder, name)


def _make_run(path: str, safety: library_safety.SafetySettings, dry_run: bool, capture: _CaptureLog,
              clock: Callable[[], float] | None, library_root: str) -> SafeRun:
    if safety.quarantine_root:
        root, quarantine = library_root, safety.quarantine_root
    else:  # the owner's rule: a dated folder beside the file
        root = quarantine = os.path.dirname(os.path.abspath(path))
    kwargs = {"clock": clock} if clock is not None else {}
    return SafeRun(
        library_root=root, quarantine_root=quarantine, log=capture, dry_run=dry_run,
        delete_after_conversion=False,  # never deletes media, whatever the setting says
        recent_minutes=safety.recent_minutes, tolerance_seconds=safety.duration_tolerance_seconds,
        tolerance_fraction=safety.duration_tolerance_fraction, **kwargs,
    )


def remux_file(
    path: str,
    *,
    dry_run: bool = True,
    safety: library_safety.SafetySettings | None = None,
    log: RemuxLog | None = None,
    cancel_event=None,
    library_root: str | None = None,
    clock: Callable[[], float] | None = None,
    probe: Callable[[str], cp.MediaInfo | None] = probe_media,
    runner: Callable = _run_ffmpeg,
    decode_check: Callable[[str], str] = conversion_run.decode_problem,
) -> RemuxResult:
    """Fixes one file's container (or reports what would be done). Never raises for an ordinary failure: the
    result says what happened and the original is untouched unless the status is REMUXED."""
    safety = safety or library_safety.get_safety_settings()
    if library_root is None:
        library_root = get_setting("rename", "library_root", "").strip()
    capture = _CaptureLog()
    run = _make_run(path, safety, dry_run, capture, clock, library_root)
    if not os.path.isfile(path):
        return RemuxResult(path, FAILED, "the file is not there")
    busy = run.busy_reason(path)
    if busy:
        return RemuxResult(path, SKIPPED, busy)
    info = probe(path)
    if info is None:
        return RemuxResult(path, FAILED, "couldn't read the file (ffprobe failed)")
    settings = cp.ConversionSettings()  # defaults: a movie, so the bitrate rules never apply to a remux
    plan = cp.plan_conversion(info, settings, is_series=False)
    if not plan.wrong_container:
        return RemuxResult(path, RIGHT, "the container matches the extension")
    if plan.skip_reason:
        return RemuxResult(path, SKIPPED, plan.skip_reason)
    if plan.video != cp.V_COPY or plan.audio_reencode or plan.bitrate_driven:
        return RemuxResult(path, SKIPPED, "would need a re-encode, which this lossless fix never does")
    final = os.path.splitext(path)[0] + plan.container
    if os.path.normcase(os.path.abspath(final)) != os.path.normcase(os.path.abspath(path)) and os.path.lexists(final):
        return RemuxResult(path, SKIPPED, f"{os.path.basename(final)} already exists (never overwritten)")
    if dry_run:
        return RemuxResult(path, WOULD, f"lossless remux from {plan.wrong_container} to a real {plan.container[1:].upper()}", final)
    original_bytes = os.path.getsize(path)
    try:
        outcome = conversion_run.convert_file(
            run, path, plan, info, settings, cancel_event=cancel_event, runner=runner, probe=probe,
            decode_check=decode_check,
        )
    except SafetyError as exc:
        _log_failure(log, path, original_bytes, str(exc))
        return RemuxResult(path, FAILED, f"the original is kept: {exc}")
    if not outcome.converted:
        message = outcome.message or "not converted"
        skipped = any(a.verb == "skip" and "left alone" in a.detail for a in run.actions)
        if message == "Cancelled":
            return RemuxResult(path, SKIPPED, "cancelled, the file is untouched")
        if not skipped:
            _log_failure(log, path, original_bytes, message)
        return RemuxResult(path, SKIPPED if skipped else FAILED, message)
    quarantined = next((new for new, old in capture.moves if old == path), "")
    new_bytes = os.path.getsize(outcome.final_path) if os.path.isfile(outcome.final_path) else 0
    if log is not None:
        log.append(path, quarantined, outcome.final_path, original_bytes, new_bytes, REMUXED)
    return RemuxResult(path, REMUXED, f"original in {quarantined}" if quarantined else outcome.message, outcome.final_path, quarantined)


def _log_failure(log: RemuxLog | None, path: str, original_bytes: int, message: str) -> None:
    if log is not None:
        log.append(path, "", "", original_bytes, 0, f"{FAILED}: {message}")


def remux_files(
    paths: list[str], *, dry_run: bool = True, safety: library_safety.SafetySettings | None = None, cancel_event=None,
    log_path: str = "", on_result: Callable[[RemuxResult], None] | None = None, **kwargs,
) -> tuple[list[RemuxResult], str]:
    """remux_file() for each path (stops between files when `cancel_event` is set). Returns (results, the TSV's
    path or "" for a dry run, which writes nothing)."""
    safety = safety or library_safety.get_safety_settings()
    paths = list(dict.fromkeys(os.path.abspath(p) for p in paths))
    log = None
    if not dry_run and paths:
        log_path = log_path or default_log_path(paths[0], safety)
        log = RemuxLog(log_path)
    results: list[RemuxResult] = []
    for path in paths:
        if cancel_event is not None and cancel_event.is_set():
            break
        result = remux_file(path, dry_run=dry_run, safety=safety, log=log, cancel_event=cancel_event, **kwargs)
        results.append(result)
        if on_result is not None:
            on_result(result)
    return results, (log.path if log is not None and os.path.isfile(log.path) else "")


# --- undo --------------------------------------------------------------------


def _free_name(path: str) -> str:
    return library_safety._free_name(path)  # noqa: SLF001 -- the quarantine's own never-overwrite naming


def undo_remux_log(
    log_path: str, day: date | None = None, *, done_status: str = REMUXED, aside_folder: str = UNDONE_FOLDER,
) -> tuple[list[str], list[str]]:
    """Reads a TSV log written by remux_files() and puts each REMUXED file back: the remuxed copy is moved (never
    deleted) into "undone remux YYYY-MM-DD" beside it, the quarantined original is moved back to its old path (refused,
    and reported, when that path is taken). Rows are undone in reverse order; a row undone before is skipped, and
    each undo is itself appended to the log. Returns (status lines, problems).

    `done_status` / `aside_folder` let core/join_files.py reuse this for its own log (rows "joined": several originals
    share one new file, which is moved aside with the first row undone; the others just come back)."""
    log = RemuxLog(log_path)
    rows = log.rows()
    already = {(r["original"], r["new"]) for r in rows if r["result"] == UNDONE}
    lines: list[str] = []
    problems: list[str] = []
    stamp = (day or date.today()).isoformat()
    for row in reversed(rows):
        if row["result"] != done_status or (row["original"], row["new"]) in already:
            continue
        original, quarantined, new = row["original"], row["quarantined"], row["new"]
        name = os.path.basename(original)
        if not quarantined or not os.path.lexists(quarantined):
            problems.append(f"{name}: the quarantined original {quarantined or '(none)'} is gone; nothing restored")
            continue
        aside = ""
        if os.path.lexists(new):
            aside = _free_name(os.path.join(os.path.dirname(os.path.abspath(new)), f"{aside_folder} {stamp}", os.path.basename(new)))
        if os.path.lexists(original) and os.path.normcase(os.path.abspath(original)) != os.path.normcase(os.path.abspath(new)):
            problems.append(f"{original} exists now; {name} left in quarantine")
            continue
        try:
            if aside:
                os.makedirs(os.path.dirname(aside), exist_ok=True)
                shutil.move(new, aside)
            os.makedirs(os.path.dirname(os.path.abspath(original)), exist_ok=True)
            shutil.move(quarantined, original)
        except OSError as exc:
            problems.append(f"{name}: {exc}")
            continue
        log.append(original, quarantined, new, 0, 0, UNDONE)
        lines.append(f"restored {original}" + (f" (the remuxed copy is in {os.path.dirname(aside)})" if aside else ""))
    return lines, problems
