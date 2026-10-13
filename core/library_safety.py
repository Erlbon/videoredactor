"""
core/library_safety.py

The safety layer every library-cleaning operation goes through (a "redact run"
that leaves files ready for a media server). It owns the hard rules:

1. NEVER delete user media: a replaced or superseded file is MOVED to a
   quarantine folder that mirrors its library-relative path, under a dated
   subfolder per kind ("remuxed originals 2026-10-13"). The ONE exception is
   the setting delete_after_conversion (off by default): when the user turned
   it on, a VERIFIED conversion deletes its original instead of quarantining it.
2. A TSV undo log, UTF-8, one line per change, written right AFTER the change
   succeeded so undoing the lines in reverse order works:
       new_path<TAB>old_path     a move or rename
       path<TAB>CREATED          a new file
3. A conversion writes to a temp name beside the source (".part" / ".resize"),
   is verified with ffprobe, and only then swapped in. A failed verification
   deletes only the temp file and keeps the original.
4. Dry-run: the same calls, nothing touched; every action is listed with its reason.
5. A file modified in the last N minutes (default 10) is never touched.

Qt-free; no Windows-only calls. Paths are always passed to tools as lists.
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable

from core.config import get_setting, set_setting

SECTION = "safety"

# The dated quarantine subfolders, one per kind of change.
KIND_REMUXED = "remuxed originals"
KIND_DUPLICATES = "duplicates"
KIND_DEFECTIVE = "defective"
KIND_CLEANUP = "cleanup"
KIND_RESIZED = "resized originals"
KIND_UNDONE = "undone"
KINDS = (KIND_REMUXED, KIND_DUPLICATES, KIND_DEFECTIVE, KIND_CLEANUP, KIND_RESIZED, KIND_UNDONE)

PART_MARKER = "part"  # <stem>.part<ext>
RESIZE_MARKER = "resize"  # <stem>.resize<ext>

DEFAULT_RECENT_MINUTES = 10
DEFAULT_DURATION_TOLERANCE_SECONDS = 2.0
DEFAULT_DURATION_TOLERANCE_FRACTION = 0.01

UNDO_CREATED = "CREATED"


class SafetyError(Exception):
    """An operation was refused or failed; nothing was lost."""


# --- settings ----------------------------------------------------------------


@dataclass
class SafetySettings:
    """Persisted in videoredactor_settings.ini, [safety]. Never hard-coded."""

    quarantine_root: str = ""  # "" = not chosen yet: nothing that replaces a file may run
    dry_run: bool = True  # the UI starts in dry-run until the user confirms
    recent_minutes: int = DEFAULT_RECENT_MINUTES
    duration_tolerance_seconds: float = DEFAULT_DURATION_TOLERANCE_SECONDS
    duration_tolerance_fraction: float = DEFAULT_DURATION_TOLERANCE_FRACTION
    undo_log_folder: str = ""  # "" = beside the quarantine folder
    delete_after_conversion: bool = False  # explicit opt-in: a verified conversion deletes its original


def _as_float(text: str, default: float) -> float:
    try:
        value = float(text)
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


def _as_int(text: str, default: int) -> int:
    try:
        value = int(text)
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


def get_safety_settings(get: Callable[[str, str, str], str] = get_setting) -> SafetySettings:
    d = SafetySettings()
    return SafetySettings(
        quarantine_root=get(SECTION, "quarantine_root", "").strip(),
        dry_run=get(SECTION, "dry_run", "1") != "0",
        recent_minutes=_as_int(get(SECTION, "recent_minutes", str(d.recent_minutes)), d.recent_minutes),
        duration_tolerance_seconds=_as_float(
            get(SECTION, "duration_tolerance_seconds", str(d.duration_tolerance_seconds)), d.duration_tolerance_seconds),
        duration_tolerance_fraction=_as_float(
            get(SECTION, "duration_tolerance_fraction", str(d.duration_tolerance_fraction)), d.duration_tolerance_fraction),
        undo_log_folder=get(SECTION, "undo_log_folder", "").strip(),
        delete_after_conversion=get(SECTION, "delete_after_conversion", "0") == "1",
    )


def set_safety_settings(s: SafetySettings, put: Callable[[str, str, str], None] = set_setting) -> None:
    put(SECTION, "quarantine_root", s.quarantine_root)
    put(SECTION, "dry_run", "1" if s.dry_run else "0")
    put(SECTION, "recent_minutes", str(s.recent_minutes))
    put(SECTION, "duration_tolerance_seconds", str(s.duration_tolerance_seconds))
    put(SECTION, "duration_tolerance_fraction", str(s.duration_tolerance_fraction))
    put(SECTION, "undo_log_folder", s.undo_log_folder)
    put(SECTION, "delete_after_conversion", "1" if s.delete_after_conversion else "0")


# --- names -------------------------------------------------------------------


def temp_name(path: str, marker: str = PART_MARKER) -> str:
    """The temp name beside `path` ("a.mkv" -> "a.part.mkv"; the real extension
    stays last so ffmpeg still picks the muxer). Same folder = same volume."""
    stem, ext = os.path.splitext(path)
    return f"{stem}.{marker}{ext}"


def mirrored_quarantine_path(path: str, library_root: str, quarantine_root: str, kind: str, day: date | None = None) -> str:
    """<quarantine_root>/<kind YYYY-MM-DD>/<path relative to library_root>.
    A file outside the library (or on another drive) goes under "_outside library"
    by its bare name so it can still be quarantined, never silently skipped."""
    stamp = (day or date.today()).isoformat()
    folder = f"{kind} {stamp}"
    path = os.path.abspath(path)
    try:
        rel = os.path.relpath(path, os.path.abspath(library_root)) if library_root else ""
    except ValueError:  # another drive
        rel = ""
    if not rel or rel == "." or rel.startswith(".."):
        rel = os.path.join("_outside library", os.path.basename(path))
    return os.path.join(quarantine_root, folder, rel)


def _free_name(path: str) -> str:
    """`path`, or "name (2).ext", "name (3).ext"... if taken: a quarantine never overwrites."""
    if not os.path.lexists(path):
        return path
    stem, ext = os.path.splitext(path)
    n = 2
    while os.path.lexists(f"{stem} ({n}){ext}"):
        n += 1
    return f"{stem} ({n}){ext}"


# --- undo log ----------------------------------------------------------------


class UndoLog:
    """Append-only TSV, UTF-8 explicitly (a mixed-encoding log was once corrupted by a blind re-encode).
    Each line is flushed and fsynced as it is written."""

    def __init__(self, path: str):
        self.path = path

    @staticmethod
    def _check(*paths: str) -> None:
        for p in paths:
            if any(c in p for c in "\t\r\n"):
                raise SafetyError(f"a tab or line break in a path can't be logged: {p!r}")

    def _append(self, line: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        with open(self.path, "a", encoding="utf-8", newline="\n") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())

    def moved(self, new_path: str, old_path: str) -> None:
        self._check(new_path, old_path)
        self._append(f"{new_path}\t{old_path}")

    def created(self, path: str) -> None:
        self._check(path)
        self._append(f"{path}\t{UNDO_CREATED}")

    def entries(self) -> list[tuple[str, str]]:
        """[(new_path, old_path or "CREATED")] in the order written."""
        if not os.path.isfile(self.path):
            return []
        out = []
        with open(self.path, encoding="utf-8", newline="") as f:
            for raw in f.read().split("\n"):
                if raw.strip() and "\t" in raw:
                    a, b = raw.split("\t", 1)
                    out.append((a, b))
        return out


def undo_log(log_path: str, quarantine_root: str, library_root: str = "", day: date | None = None) -> list[str]:
    """Undoes a log in REVERSE order. A move is moved back (refused, and reported, if the old path is taken
    now); a CREATED file is moved into the "undone" quarantine folder, never deleted. Returns the problems."""
    problems: list[str] = []
    for new, old in reversed(UndoLog(log_path).entries()):
        try:
            if old == UNDO_CREATED:
                if os.path.lexists(new):
                    dest = _free_name(mirrored_quarantine_path(new, library_root, quarantine_root, KIND_UNDONE, day))
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    shutil.move(new, dest)
            else:
                if not os.path.lexists(new):
                    problems.append(f"{new} is gone; can't move it back to {old}")
                elif os.path.lexists(old):
                    problems.append(f"{old} exists now; {new} left where it is")
                else:
                    os.makedirs(os.path.dirname(os.path.abspath(old)), exist_ok=True)
                    shutil.move(new, old)
        except OSError as exc:
            problems.append(f"{new}: {exc}")
    return problems


# --- verification ------------------------------------------------------------


def verify_streams(
    before: tuple[dict[str, int], float | None] | None,
    after: tuple[dict[str, int], float | None] | None,
    tolerance_seconds: float = DEFAULT_DURATION_TOLERANCE_SECONDS,
    tolerance_fraction: float = DEFAULT_DURATION_TOLERANCE_FRACTION,
) -> str:
    """Compares two ffprobe summaries ({codec_type: count}, duration). "" = good, otherwise the problem.
    Rule 3: video present, the SAME number of audio and subtitle tracks, duration within tolerance
    (either direction). Takes the summaries so it is testable without ffprobe."""
    if before is None or after is None:
        return "couldn't compare the result with the original (ffprobe unavailable or failed)"
    problems = []
    if after[0].get("video", 0) < 1:
        problems.append("no video track")
    for kind in ("audio", "subtitle"):
        if before[0].get(kind, 0) != after[0].get(kind, 0):
            problems.append(f"{kind} tracks: {before[0].get(kind, 0)} before, {after[0].get(kind, 0)} after")
    if before[1]:
        if after[1] is None:
            problems.append("no duration")
        elif abs(after[1] - before[1]) > max(tolerance_seconds, before[1] * tolerance_fraction):
            problems.append(f"duration {after[1]:.0f}s vs {before[1]:.0f}s")
    return ", ".join(problems)


# --- the run -----------------------------------------------------------------


@dataclass
class Action:
    verb: str  # "quarantine", "rename", "convert", "skip"
    path: str
    detail: str  # what happens
    reason: str  # why
    done: bool = False

    def __str__(self) -> str:
        return f"{self.verb}: {self.path} -> {self.detail} ({self.reason})"


@dataclass
class SafeRun:
    """One batch. All mutating calls go through here so dry-run, the recent-file guard,
    quarantine and the undo log cannot be bypassed. `actions` is the report."""

    library_root: str
    quarantine_root: str
    log: UndoLog | None = None
    dry_run: bool = True
    delete_after_conversion: bool = False
    recent_minutes: int = DEFAULT_RECENT_MINUTES
    tolerance_seconds: float = DEFAULT_DURATION_TOLERANCE_SECONDS
    tolerance_fraction: float = DEFAULT_DURATION_TOLERANCE_FRACTION
    day: date = field(default_factory=date.today)
    clock: Callable[[], float] = time.time
    probe: Callable[[str], tuple[dict[str, int], float | None] | None] | None = None  # ffmpeg_backend._stream_summary
    actions: list[Action] = field(default_factory=list)

    def _note(self, verb: str, path: str, detail: str, reason: str, done: bool = False) -> Action:
        a = Action(verb, path, detail, reason, done)
        self.actions.append(a)
        return a

    def is_recent(self, path: str) -> bool:
        try:
            return self.clock() - os.path.getmtime(path) < self.recent_minutes * 60
        except OSError:
            return True  # can't tell: leave it alone

    def busy_reason(self, path: str) -> str:
        if self.is_recent(path):
            return f"modified in the last {self.recent_minutes} minutes (may still be downloading or being written)"
        return ""

    def _require_quarantine(self) -> None:
        if not self.quarantine_root:
            raise SafetyError("no quarantine folder is set; nothing that replaces or removes a file may run")

    def quarantine(self, path: str, kind: str, reason: str) -> str | None:
        """Moves `path` into the dated quarantine folder, mirroring its library-relative path.
        Returns the destination (the would-be one in dry-run), None if the file was skipped."""
        self._require_quarantine()
        busy = self.busy_reason(path)
        if busy:
            self._note("skip", path, "left alone", busy)
            return None
        dest = _free_name(mirrored_quarantine_path(path, self.library_root, self.quarantine_root, kind, self.day))
        action = self._note("quarantine", path, dest, reason)
        if self.dry_run:
            return dest
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.move(path, dest)
        if self.log:
            self.log.moved(dest, path)
        action.done = True
        return dest

    def rename(self, src: str, dst: str, reason: str) -> bool:
        """Renames/moves without ever overwriting."""
        busy = self.busy_reason(src)
        if busy:
            self._note("skip", src, "left alone", busy)
            return False
        if os.path.lexists(dst) and os.path.normcase(os.path.abspath(src)) != os.path.normcase(os.path.abspath(dst)):
            self._note("skip", src, f"{dst} already exists", reason)
            return False
        action = self._note("rename", src, dst, reason)
        if self.dry_run:
            return True
        os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
        os.rename(src, dst)
        if self.log:
            self.log.moved(dst, src)
        action.done = True
        return True

    def convert(
        self,
        source: str,
        final: str,
        build: Callable[[str, str], tuple[bool, str]],
        kind: str,
        reason: str,
        marker: str = PART_MARKER,
    ) -> bool:
        """build(source, temp) -> (ok, message) writes the new file to `temp`. The temp is verified (rule 3),
        the original goes to quarantine `kind`, then the temp takes `final`'s name. On any failure only the temp
        is removed. `final` may equal `source` (same name) or differ (new extension); an existing `final` that is
        not `source` is never replaced."""
        if not self.delete_after_conversion:
            self._require_quarantine()
        busy = self.busy_reason(source)
        if busy:
            self._note("skip", source, "left alone", busy)
            return False
        same = os.path.normcase(os.path.abspath(source)) == os.path.normcase(os.path.abspath(final))
        if not same and os.path.lexists(final):
            self._note("skip", source, f"{final} already exists", reason)
            return False
        action = self._note("convert", source, final, reason)
        if self.dry_run:
            return True
        temp = temp_name(final, marker)
        try:
            if os.path.lexists(temp):
                raise SafetyError(f"{temp} is in the way (a leftover?); left alone")
            ok, message = build(source, temp)
            if not ok or not os.path.isfile(temp) or os.path.getsize(temp) == 0:
                raise SafetyError(f"conversion failed: {message or 'no output'}")
            probe = self.probe
            if probe is None:
                from core.ffmpeg_backend import _stream_summary as probe  # noqa: PLC0415
            problem = verify_streams(probe(source), probe(temp), self.tolerance_seconds, self.tolerance_fraction)
            if problem:
                raise SafetyError(f"verification failed: {problem}")
        except SafetyError as exc:
            _remove_temp(temp)
            action.verb, action.detail = "skip", f"original kept ({exc})"
            return False
        except BaseException:
            _remove_temp(temp)
            raise
        if self.delete_after_conversion:
            # The user's explicit setting: the verified result replaces the original for good (not undoable).
            try:
                if same:
                    os.replace(temp, final)
                else:
                    os.remove(source)
                    os.rename(temp, final)
            except OSError as exc:
                raise SafetyError(f"couldn't swap the result in ({exc}); result at {temp}") from exc
            action.detail = f"{final} (original deleted: delete-after-conversion is on)"
            if self.log:
                self.log.created(final)
            action.done = True
            return True
        # Verified. Original aside first (it exists under SOME name at every instant), then the temp in.
        dest = _free_name(mirrored_quarantine_path(source, self.library_root, self.quarantine_root, kind, self.day))
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.move(source, dest)
        except OSError as exc:
            _remove_temp(temp)
            action.verb, action.detail = "skip", f"original kept (couldn't quarantine it: {exc})"
            return False
        if self.log:
            self.log.moved(dest, source)
        try:
            os.rename(temp, final)
        except OSError as exc:
            # Put the original back; the temp stays for the user (it is a complete, verified file).
            try:
                shutil.move(dest, source)
                if self.log:
                    self.log.moved(source, dest)
            except OSError:
                pass
            raise SafetyError(f"couldn't move the result into place ({exc}); original at {source if os.path.exists(source) else dest}, result at {temp}") from exc
        if self.log:
            self.log.created(final)
        action.done = True
        return True


def _remove_temp(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def stamp_now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H%M%S")
