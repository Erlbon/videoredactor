"""
core/history.py

The operations journal (redactor_common.core.journal) for this app: every TAG change the app makes to a file
(a plain Save / Save All, the command line's `set` and `check --stamp`, a Redact save, a Restore from
Tools > History) is recorded with the old and new text of each changed field, so an unwanted change can be
undone after the app was closed. Covers and other binary data are not kept; replaced or converted files are
covered by the Recycle Bin / quarantine, not by this.

Opt-in, off by default: history_enabled() / set_history_enabled(); while off nothing is written.

What this module adds to the shared journal:
  get_journal()        the process-wide Journal, `videoredactor_journal.jsonl` next to videoredactor_settings.ini
  fields_text(md)      the editable fields as plain text: what a journal entry compares (no technical fields,
                       no scan stamp, so a save that only records a Check File stamp records nothing)
  apply_restore(...)   the History dialog's writer: old values back onto the file with the app's own save path
  journal_begin/finish wrappers that never raise: a journal problem must never block or fail a save; it is
                       reported once (stderr, and take_problem() for the window's status bar)
  record_moves(pairs)  entries follow a renamed, moved or converted file (the RenameLog on_move hook)

Qt-free. apply_restore runs on a worker thread (the dialog's run_busy).
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import Iterable, Mapping, Optional

from redactor_common.core.journal import Journal, default_journal_path, diff_fields

from core import config
from core.app_paths import APP_SLUG
from core.filename_pattern import field_text, set_field_text
from core.video_metadata import EDITABLE_FIELDS, VideoMetadata

# OPT-IN (owner decision 2026-10-14: no logging of a lot of data without a user request). Off by default; turned
# on in Tools > Preferences > History or by answering Yes in Tools > History. While it is off nothing is
# written: no journal file is created, no line appended (GatedJournal below), on the command line too.
SETTINGS_SECTION = "history"
SETTINGS_KEY = "enabled"
_TRUE = frozenset({"1", "true", "yes", "on"})

_lock = threading.Lock()
_journal: Optional[Journal] = None
_problem_reported = False
_pending_problem = ""


def history_enabled() -> bool:
    """Whether tag changes are recorded ([history] enabled in videoredactor_settings.ini). A missing or garbled
    value, or an unreadable settings file, means off."""
    try:
        return config.get_setting(SETTINGS_SECTION, SETTINGS_KEY, "").strip().lower() in _TRUE
    except Exception:  # noqa: BLE001 -- a broken settings file must not break a save
        return False


def set_history_enabled(enabled: bool) -> None:
    config.set_setting(SETTINGS_SECTION, SETTINGS_KEY, "true" if enabled else "false")


class GatedJournal(Journal):
    """A Journal that writes nothing while history is off (reading still works, for a journal kept from when
    it was on). The History dialog's own restore entries go through here too."""

    def begin(self, *args, **kwargs):
        return super().begin(*args, **kwargs) if history_enabled() else None

    def mark_restored(self, entry_id, restore_id):
        if history_enabled():
            super().mark_restored(entry_id, restore_id)

    def record_moves(self, pairs):
        if history_enabled():
            super().record_moves(pairs)

    # finish() needs no gate: it ignores the None id begin() returns while off, and an id begun while on
    # must still be finished.


def journal_path() -> str:
    """`videoredactor_journal.jsonl` next to videoredactor_settings.ini."""
    return default_journal_path(APP_SLUG, os.path.dirname(os.path.abspath(os.fspath(config.CONFIG_PATH))))


def get_journal() -> Journal:
    """The process-wide journal (a GatedJournal), created on first use; creating it writes nothing."""
    global _journal
    with _lock:
        if _journal is None:
            _journal = GatedJournal(journal_path())
        return _journal


def journal_exists() -> bool:
    return os.path.isfile(get_journal().path)


def delete_history(trash=None) -> str:
    """Moves the journal file (and a leftover prune temp file) to the Recycle Bin (`trash`, injectable for
    tests). Returns "" or the problem."""
    if trash is None:
        from redactor_common.core.trash import move_to_trash as trash  # noqa: PLC0415
    path = get_journal().path
    try:
        for candidate in (path, path + ".tmp"):
            if os.path.exists(candidate):
                trash(candidate)
    except Exception as exc:  # noqa: BLE001 -- TrashError or OSError: reported, not raised
        return str(exc)
    return ""


def set_journal(journal: Optional[Journal]) -> None:
    """Replaces the process-wide journal (tests point it at tmp_path); None makes the next get_journal() create
    the default one again. Also forgets an earlier reported problem."""
    global _journal, _problem_reported, _pending_problem
    with _lock:
        _journal = journal
        _problem_reported = False
        _pending_problem = ""


def fields_text(metadata: VideoMetadata) -> dict[str, str]:
    """{field: text} of every editable tag field, through the app's own field_text()."""
    return {name: field_text(metadata, name) for name in EDITABLE_FIELDS}


# --- journaling that never fails a save ---------------------------------------------------------


def _report(exc: BaseException) -> None:
    global _problem_reported, _pending_problem
    with _lock:
        if _problem_reported:
            return
        _problem_reported = True
        _pending_problem = f"The change history (Tools > History) could not be written: {exc}"
        message = _pending_problem
    try:
        if sys.stderr is not None:
            print(message, file=sys.stderr)
    except Exception:  # noqa: BLE001 -- a windowed exe may have no usable stderr
        pass


def take_problem() -> str:
    """The journal problem not shown yet ("" if none); shown once, then cleared."""
    global _pending_problem
    with _lock:
        problem, _pending_problem = _pending_problem, ""
        return problem


def journal_begin(
    path, kind: str, old: Mapping[str, str], new: Mapping[str, str], note: str = "",
) -> Optional[str]:
    """Announces a change (before the file is written). None when history is off, nothing changed or the
    journal failed."""
    if not history_enabled():
        return None
    try:
        changes = diff_fields(old, new)
        if not changes:
            return None
        return get_journal().begin(os.fspath(path), kind, changes, note=note)
    except Exception as exc:  # noqa: BLE001 -- never block a save over the history
        _report(exc)
        return None


def journal_finish(entry_id: Optional[str], ok: bool, error: str = "") -> None:
    if entry_id is None:
        return
    try:
        get_journal().finish(entry_id, ok, error)
    except Exception as exc:  # noqa: BLE001
        _report(exc)


def record_moves(pairs: Iterable[tuple[object, object]]) -> None:
    """The file at each `old` is now at `new` (a rename, a move, or a conversion that changed the name or
    extension): its journal entries follow it. The hook every RenameLog of this app gets as `on_move`, and what
    the command line, Redact (without a rename log) and the conversions call themselves. Does nothing while
    history is off; never raises (a journal problem is reported once, like a save's)."""
    if not history_enabled():
        return
    try:
        cleaned = [(os.fspath(old), os.fspath(new)) for old, new in pairs]
        if cleaned:
            get_journal().record_moves(cleaned)
    except Exception as exc:  # noqa: BLE001 -- bookkeeping must never fail the rename that already happened
        _report(exc)


def record_move(old_path, new_path) -> None:
    """record_moves() for one file."""
    record_moves([(old_path, new_path)])


# --- restore ------------------------------------------------------------------------------------


def apply_restore(path: str, values: Mapping[str, str]) -> str:
    """Writes {field: text} onto `path` with the app's own reader and writer (load the file's CURRENT tags, set
    the given fields, save: write, re-read, compare). Returns "" or a plain-language problem. Runs on a worker
    thread: no widgets. Writes no journal entry (the History dialog records the restore itself)."""
    from core.video_file import VideoFile  # here, not at the top: video_file imports this module

    video = VideoFile(path=Path(path))
    video.load()
    if video.load_error:
        return f"the file could not be read ({video.load_error})"
    unknown = [name for name in values if name not in EDITABLE_FIELDS]
    if unknown:
        return "this program has no field called " + ", ".join(unknown)
    refused = [name for name, text in values.items() if not set_field_text(video.metadata, name, text)]
    if refused:
        return "the old value does not fit the field " + ", ".join(refused)
    video.dirty = True
    video.save(journal=False)
    return video.save_error
