"""
core/library_safety.py

The safety layer every library-cleaning operation goes through (a "redact run"
that leaves files ready for a media server). It owns the hard rules:

1. NEVER delete user media: a replaced or superseded file is MOVED to a
   quarantine folder that mirrors its library-relative path, under a dated
   subfolder per kind ("remuxed originals 2026-10-13"). The quarantine folder is
   OPTIONAL: with none set, a replaced file goes to the Recycle Bin (the app's
   normal behaviour) and the TSV undo log is only kept when a log is given. The ONE exception is
   the setting delete_after_conversion (off by default): when the user turned
   it on, a VERIFIED conversion deletes its original instead of quarantining it.
2. A TSV undo log, UTF-8, one line per change, written right AFTER the change
   succeeded so undoing the lines in reverse order works:
       new_path<TAB>old_path     a move or rename
       path<TAB>CREATED          a new file
3. A conversion writes to a temp name beside the source (".redact-part" / ".redact-resize"),
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
KIND_JOINED = "joined originals"
KINDS = (KIND_REMUXED, KIND_DUPLICATES, KIND_DEFECTIVE, KIND_CLEANUP, KIND_RESIZED, KIND_UNDONE, KIND_JOINED)

# The only containers the app produces.
OUTPUT_EXTENSIONS = (".mp4", ".mkv")

# Conversion temp names. Deliberately "redact-" prefixed (redactor_common.core.temp_names recognises exactly these):
# the bare words would hide a real file such as "Kill.Bill.Part.mkv" from Open Folder as a "temp file".
PART_MARKER = "redact-part"  # <stem>.redact-part<ext>
RESIZE_MARKER = "redact-resize"  # <stem>.redact-resize<ext>

DEFAULT_RECENT_MINUTES = 10
# The owner's rule: the converted file's duration is within 1.5 s of the source, whatever its length. The share of the
# length is an optional extra allowance for people who want one (default none).
DEFAULT_DURATION_TOLERANCE_SECONDS = 1.5
DEFAULT_DURATION_TOLERANCE_FRACTION = 0.0

UNDO_CREATED = "CREATED"


class SafetyError(Exception):
    """An operation was refused or failed; nothing was lost."""


# --- settings ----------------------------------------------------------------


@dataclass
class SafetySettings:
    """Persisted in videoredactor_settings.ini, [safety]. Never hard-coded."""

    quarantine_root: str = ""  # "" = no quarantine: replaced files go to the Recycle Bin
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
    """The temp name beside `path` ("a.mkv" -> "a.redact-part.mkv"; the real extension
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
    trash: Callable[[str], None] | None = None  # Recycle Bin mover when no quarantine folder is set
    known_mtime: dict[str, float] = field(default_factory=dict)  # path -> modified time to trust instead of the disk's
    probe: Callable[[str], tuple[dict[str, int], float | None] | None] | None = None  # ffmpeg_backend._stream_summary
    actions: list[Action] = field(default_factory=list)

    def _note(self, verb: str, path: str, detail: str, reason: str, done: bool = False) -> Action:
        a = Action(verb, path, detail, reason, done)
        self.actions.append(a)
        return a

    def is_recent(self, path: str) -> bool:
        try:
            modified = self.known_mtime.get(path)
            if modified is None:
                modified = os.path.getmtime(path)
            return self.clock() - modified < self.recent_minutes * 60
        except OSError:
            return True  # can't tell: leave it alone

    def busy_reason(self, path: str) -> str:
        if self.is_recent(path):
            return f"modified in the last {self.recent_minutes} minutes (may still be downloading or being written)"
        return ""

    def _trash(self, path: str) -> None:
        if self.trash is None:
            from redactor_common.core.trash import move_to_trash  # noqa: PLC0415
            move_to_trash(path)
        else:
            self.trash(path)

    def quarantine(self, path: str, kind: str, reason: str) -> str | None:
        """Moves `path` into the dated quarantine folder, mirroring its library-relative path.
        Returns the destination (the would-be one in dry-run), None if the file was skipped."""
        busy = self.busy_reason(path)
        if busy:
            self._note("skip", path, "left alone", busy)
            return None
        if not self.quarantine_root:
            self._note("recycle", path, "Recycle Bin", reason)
            if self.dry_run:
                return "Recycle Bin"
            self._trash(path)
            self.actions[-1].done = True
            return "Recycle Bin"
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

    def dispose(self, path: str, kind: str, reason: str) -> bool:
        """A file that has served its purpose (an external subtitle now inside the video): deleted when
        delete_after_conversion is on, else quarantined / Recycle Bin like any replaced file."""
        if not self.delete_after_conversion:
            return self.quarantine(path, kind, reason) is not None
        busy = self.busy_reason(path)
        if busy:
            self._note("skip", path, "left alone", busy)
            return False
        action = self._note("delete", path, "deleted (delete-after-conversion is on)", reason)
        if not self.dry_run:
            os.remove(path)
            action.done = True
        return True

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
        verify: Callable[[str, str], str] | None = None,
    ) -> bool:
        """build(source, temp) -> (ok, message) writes the new file to `temp`. The temp is verified (rule 3),
        the original goes to quarantine `kind`, then the temp takes `final`'s name. On any failure only the temp
        is removed. `final` may equal `source` (same name) or differ (new extension); an existing `final` that is
        not `source` is never replaced."""
        if os.path.splitext(final)[1].lower() not in OUTPUT_EXTENSIONS:
            raise SafetyError(f"{final}: only MP4 and MKV files are produced")
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
            if verify is not None:  # the caller's own check: (source, temp) -> problem or ""
                problem = verify(source, temp)
            else:
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
        # Verified. The swap itself is redactor_common's commit_in_place: the result takes its name first
        # (a different name) or the original is set aside as "<stem>.redact-orig<ext>" first (same name), so
        # the original exists under SOME name at every instant, every rename is retried on a lock, and the
        # set-aside original is only handed to `disposer` once the result is in place. A disposer that fails
        # keeps the original beside the result (never a delete).
        from redactor_common.core.pipeline import CommitError, commit_in_place  # noqa: PLC0415

        if self.delete_after_conversion:
            # The user's explicit setting: the verified result replaces the original for good (not undoable).
            disposer, how = os.remove, "original deleted: delete-after-conversion is on"
        elif self.quarantine_root:
            dest = _free_name(mirrored_quarantine_path(source, self.library_root, self.quarantine_root, kind, self.day))

            def disposer(set_aside: str) -> None:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.move(set_aside, dest)
                if self.log:
                    self.log.moved(dest, source)

            how = f"original in {dest}"
        else:
            disposer, how = self._trash, "original in the Recycle Bin"
        try:
            result = commit_in_place(source, temp, disposer, None, new_path=None if same else final)
        except CommitError as exc:
            # The original is intact (commit_in_place rolled back); a complete, verified result may still be
            # at the temp name, which the message names so the user can keep it.
            where = f"; result at {temp}" if os.path.lexists(temp) else ""
            raise SafetyError(f"couldn't swap the result in ({exc}){where}") from exc
        if result.backup_kept:
            action.detail = f"{final} (the original is kept at {result.backup}: {result.warning})"
        else:
            action.detail = f"{final} ({how})"
        if self.log and (self.quarantine_root or self.delete_after_conversion):
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
