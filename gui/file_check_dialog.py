"""
gui/file_check_dialog.py

Media > Check Files... (core/file_check.py): the quick check for
the selected files (or all loaded ones), then a results dialog listing
every file with something to report, and Repair for those a lossless
remux can fix -- each original goes to the Recycle Bin.

Each file is checked/repaired on a worker thread
(redactor_common's call_in_background), so the window keeps painting
while ffmpeg reads a multi-GB file; Cancel takes effect between files.
Results land on VideoFile.check and show in the Status column
(DAMAGED / WRONG CONTAINER / REPAIRABLE / NOTE / CHECKED OK, the findings as a tooltip).
A WRONG CONTAINER file (an .mp4 that is really an FLV) is fixed by "Remux to Real Container"
(core/container_remux.py): lossless, verified, original moved to a quarantine folder, TSV undo log.
Each real result is also stamped into the file's metadata (status, time,
video fingerprint -- VideoFile.record_check), which marks the file unsaved;
Save writes it inside the file.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)
from redactor_common.gui.background_call import call_in_background
from redactor_common.gui.progress import run_with_progress
from redactor_common.gui.sortable_table import NaturalTableWidgetItem, enable_sorting
from redactor_common.gui.table_columns import make_columns_resizable
from redactor_common.gui.work_progress import run_work_with_progress

from core import container_remux, library_safety
from core.config import get_setting, set_setting
from core.cut_damaged import CutCancelled, cut_out_damaged, plan_cut
from core.deep_scan import DeepScanCancelled
from core.file_check import deep_tooltip, quick_check, repair
from core.video_file import VideoFile
from core.video_fingerprint import video_fingerprint
from redactor_common.gui.owned_dialog import OwnedDialog

TITLE = "Check Files"
DEEP_SCAN_LABEL = "Deep scan (reads the whole file)"
CUT_BUTTON = "Cut Out Damaged Parts"
REMUX_BUTTON = "Remux to Real Container"
LANGUAGE_BUTTON = "Set Track Language"
_SETTINGS_SECTION = "file_check"
_DEEP_KEY = "deep_scan"


def remembered_deep_scan() -> bool:
    return get_setting(_SETTINGS_SECTION, _DEEP_KEY, "0") == "1"


def ask_check_options(window, count: int) -> Optional[bool]:
    """The small Check Files dialog before the check: the "Deep scan (reads the whole file)" checkbox, default OFF and
    remembered. Returns whether to deep scan, or None when cancelled."""
    dialog = QDialog(window)
    dialog.setWindowTitle(TITLE)
    layout = QVBoxLayout(dialog)
    label = QLabel(
        f"Check {count} file(s) for damage?<br>The ordinary check looks at the index, the tracks and the first and "
        "last 8 MB of the media data (seconds per file)."
    )
    label.setWordWrap(True)
    layout.addWidget(label)
    box = QCheckBox(DEEP_SCAN_LABEL)
    box.setChecked(remembered_deep_scan())
    box.setToolTip(
        "Reads every byte once to find blocks of zeros inside the video data (pieces that were never downloaded). "
        "Slow on big files and on a network share; the quick check can't see those blocks."
    )
    layout.addWidget(box)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Check")
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    dialog.checkbox = box  # for the tests
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    set_setting(_SETTINGS_SECTION, _DEEP_KEY, "1" if box.isChecked() else "0")
    return box.isChecked()


FIX_ORDER = ("remux", "cut", "repair", "language")  # the order "Fix All" runs the steps in, per file


def eligible_fixes(files) -> dict[str, list[VideoFile]]:
    """For each fix step, the checked files it applies to RIGHT NOW (from each file's current check result)."""
    return {
        "remux": [vf for vf in files if vf.check and getattr(vf.check, "can_remux_container", False)],
        "cut": [vf for vf in files if vf.check and getattr(vf.check, "can_cut", False)],
        "repair": [vf for vf in files if vf.check and vf.check.can_repair],
        "language": [
            vf for vf in files
            if vf.check and any(f.code == "no_language" for f in vf.check.findings)
            and vf.path.suffix.lower() in (".mp4", ".m4v", ".mkv")
        ],
    }


class FileCheckResultsDialog(OwnedDialog):
    """The files with findings, worst first; "Repair" accepts the dialog (`action` "repair"), "Cut Out Damaged
    Parts..." accepts it with `action` "cut", "Fix All" with `action` "all" (every step that applies, in order)."""

    def __init__(self, checked: list[VideoFile], parent=None):
        super().__init__(parent)
        self.setWindowTitle(TITLE)
        self.resize(900, 420)
        self.action = "repair"
        order = {"DAMAGED": 0, "WRONG CONTAINER": 1, "REPAIRABLE": 1, "NOTE": 2, "CHECKED OK": 3}
        reported = sorted(
            (vf for vf in checked if vf.check and vf.check.findings),
            key=lambda vf: (order.get(vf.check.status, 9), vf.path.name.lower()),
        )
        self.checked = checked
        fixes = eligible_fixes(checked)
        self.repairable, self.cuttable = fixes["repair"], fixes["cut"]
        self.remuxable, self.language_files = fixes["remux"], fixes["language"]
        kinds = sum(1 for step in FIX_ORDER if fixes[step])
        counts = {status: sum(1 for vf in checked if vf.check and vf.check.status == status) for status in order}

        layout = QVBoxLayout(self)
        text = (
            f"Checked {len(checked)} file(s): {counts['CHECKED OK']} OK, {counts['DAMAGED']} damaged, "
            f"{counts['REPAIRABLE']} repairable, {counts['WRONG CONTAINER']} with a wrong container, "
            f"{counts['NOTE']} with notes only.<br>"
            "<b>Repair</b> rewrites a file losslessly (no re-encoding): it rebuilds a missing seek "
            "index or duration, moves an MP4's index to the front, fixes default audio tracks, and "
            "for a damaged file keeps everything readable -- what's missing can't be restored. "
            "Tags and cover are kept; each original goes to the Recycle Bin."
        )
        if self.cuttable:
            text += (
                f"<br><b>{CUT_BUTTON}</b> is for a file with zero-filled blocks inside the video (a Deep scan "
                "found them): a repair can't bring those parts back, but this removes just them, losslessly, and "
                "keeps the rest; the original goes to the Recycle Bin."
            )
        if self.remuxable:
            text += (
                f"<br><b>{REMUX_BUTTON}</b> is for a file whose container is not what its extension says (an .mp4 "
                "that is really an FLV: players report file errors, the content is intact). It is remuxed "
                "losslessly into a real MP4/MKV, verified (container, length, tracks, a full decode), and only then "
                "swapped in; the original is MOVED to a 'remuxed originals' folder (never deleted) and a log "
                "allows an undo. You can choose a dry run first."
            )
        if self.language_files:
            text += (
                f"<br><b>{LANGUAGE_BUTTON}</b> is for a file with audio or subtitle tracks that have no language tag "
                "(a note only): it writes the language of the tracks inside the file, without re-encoding or rewriting it."
            )
        summary = QLabel(text)
        summary.setWordWrap(True)
        layout.addWidget(summary)

        self.table = QTableWidget(len(reported), 3)
        self.table.setHorizontalHeaderLabels(["File", "Status", "Findings"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        for row, vf in enumerate(reported):
            for col, cell in enumerate((vf.path.name, vf.check.status, vf.check.summary())):
                item = NaturalTableWidgetItem(cell)
                tip = cell if col != 2 or vf.check.deep is None or not vf.check.deep.spans else (
                    cell + chr(10) + deep_tooltip(vf.check.deep))
                item.setToolTip(tip)
                self.table.setItem(row, col, item)
        make_columns_resizable(self.table, wide_column=2)
        enable_sorting(self.table)  # display only: no row is looked up by number
        layout.addWidget(self.table)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.fix_all_button: Optional[QPushButton] = None
        if kinds >= 2:  # one click for a file (or batch) that needs more than one kind of fix
            fixable = {id(vf) for step in FIX_ORDER for vf in fixes[step]}
            self.fix_all_button = buttons.addButton(
                f"Fix All ({len(fixable)} file(s))...", QDialogButtonBox.ButtonRole.AcceptRole)
            self.fix_all_button.setDefault(True)
            self.fix_all_button.clicked.connect(self._choose_all)
        self.repair_button: Optional[QPushButton] = None
        self.cut_button: Optional[QPushButton] = None
        if self.repairable:
            self.repair_button = buttons.addButton(
                f"Repair {len(self.repairable)} File(s)...", QDialogButtonBox.ButtonRole.AcceptRole
            )
        if self.cuttable:
            self.cut_button = buttons.addButton(f"{CUT_BUTTON}...", QDialogButtonBox.ButtonRole.ActionRole)
            self.cut_button.clicked.connect(self._choose_cut)
        self.remux_button: Optional[QPushButton] = None
        if self.remuxable:
            self.remux_button = buttons.addButton(
                f"{REMUX_BUTTON} ({len(self.remuxable)})...", QDialogButtonBox.ButtonRole.ActionRole)
            self.remux_button.clicked.connect(self._choose_remux)
        self.language_button: Optional[QPushButton] = None
        if self.language_files:
            self.language_button = buttons.addButton(
                f"{LANGUAGE_BUTTON}... ({len(self.language_files)})", QDialogButtonBox.ButtonRole.ActionRole)
            self.language_button.clicked.connect(self._choose_language)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _choose_all(self) -> None:
        self.action = "all"
        self.accept()

    def _choose_language(self) -> None:
        self.action = "language"
        self.accept()

    def _choose_remux(self) -> None:
        self.action = "remux"
        self.accept()

    def _choose_cut(self) -> None:
        self.action = "cut"
        self.accept()


def _check_and_fingerprint(path: str, deep: bool = False, progress=None, cancel=None):
    """The check plus the video fingerprint it is stamped with, for
    one worker call. The fingerprint is skipped when there was no real scan."""
    result = quick_check(path, deep=True, progress=progress, cancel=cancel) if deep else quick_check(path)
    if not result.openable:
        return result, ""
    return result, video_fingerprint(path)


def _file_size(vf: VideoFile) -> int:
    try:
        return max(os.path.getsize(vf.path), 1)
    except OSError:
        return 1


def check_files(
    window, files: list[VideoFile], errors: Optional[list[str]] = None, deep: bool = False,
) -> list[VideoFile]:
    """Runs the check on `files`; returns the ones checked (fewer
    if cancelled). A file whose check blows up (OSError, a timeout...) is
    appended to `errors` and the rest still run. With `deep` the whole of every file is read (one counted progress
    dialog over all the bytes, Cancel takes effect within a chunk)."""
    checked: list[VideoFile] = []
    if deep:
        return _deep_check_files(window, files, errors, checked)

    def step(vf: VideoFile, _index: int) -> None:
        try:
            result, fingerprint = call_in_background(_check_and_fingerprint, str(vf.path))
        except Exception as exc:
            if errors is not None:
                errors.append(f"{vf.path.name}: {exc}")
            return
        vf.record_check(result, fingerprint)
        checked.append(vf)

    run_with_progress(window, files, step, "Checking files...", threshold=1,
                      label_for=lambda vf: f"Checking {vf.path.name}")
    return checked


def _deep_check_files(window, files, errors, checked) -> list[VideoFile]:
    sizes = [_file_size(vf) for vf in files]
    total = sum(sizes)

    def work(progress):
        done: list[tuple[VideoFile, object, str, Optional[str]]] = []
        base = 0
        for vf, size in zip(files, sizes):
            if progress.cancelled.is_set():
                break
            progress.set_label(f"Deep scan: {vf.path.name} (reads the whole file)")

            def on_bytes(read, of, base=base, size=size):
                progress.set_value(base + int(size * min(read / max(of, 1), 1.0)))

            try:
                result, fingerprint = _check_and_fingerprint(str(vf.path), True, on_bytes, progress.cancelled)
                done.append((vf, result, fingerprint, None))
            except DeepScanCancelled:
                break
            except Exception as exc:  # OSError, a timeout...: the rest still run
                done.append((vf, None, "", str(exc)))
            base += size
            progress.set_value(base)
        return done

    done, _cancelled = run_work_with_progress(window, total, "Deep scan...", work, cancellable=True, show_after_ms=0)
    for vf, result, fingerprint, error in done:
        if error is not None:
            if errors is not None:
                errors.append(f"{vf.path.name}: {error}")
            continue
        vf.record_check(result, fingerprint)
        checked.append(vf)
    return checked


def unsaved(files) -> list[VideoFile]:
    """The files with edits that are not written yet (a changed check stamp alone is not an edit)."""
    return [vf for vf in files if vf.dirty and not vf.stamp_only_dirty]


def save_pending_first(window, files: list[VideoFile]) -> tuple[list[VideoFile], list[str], set]:
    """A repair rewrites the file from what is on disk, so edits that are only in memory would be lost or refused:
    the files with unsaved edits are saved FIRST, through the same path as Save All (progress, journal, failure
    box). Returns (the files that were saved, one error line per file whose save failed, the ids of those files:
    they are not repaired)."""
    pending = unsaved(files)
    if not pending or window is None or not hasattr(window, "_save_files"):
        return [], [], set()
    saveable, skipped = window._split_saveable(pending)
    if saveable:
        window._save_files(saveable, skipped_error_count=len(skipped))
    saved, errors, not_ready = [], [], set()
    for vf in pending:
        if vf in skipped:
            errors.append(f"{vf.path.name}: not repaired -- it failed to load, so its changes cannot be saved")
            not_ready.add(id(vf))
        elif vf.save_error or (vf.dirty and not vf.stamp_only_dirty):
            errors.append(f"{vf.path.name}: not repaired -- saving its unsaved changes first failed: "
                          f"{vf.save_error or 'it is still marked unsaved'}")
            not_ready.add(id(vf))
        else:
            saved.append(vf)
    return saved, errors, not_ready


def repair_files(window, files: list[VideoFile]) -> tuple[list[VideoFile], list[str], list[str]]:
    """Repairs `files` (each already checked and repairable); returns (repaired, errors, notes). Files with
    unsaved edits are saved first (see save_pending_first); a file whose save fails is not repaired and is
    reported with its save error. The per-file guard below stays as a safety net (it should never fire)."""
    repaired: list[VideoFile] = []
    errors: list[str] = []
    notes: list[str] = []
    saved_first, save_errors, not_ready = save_pending_first(window, files)
    errors.extend(save_errors)
    ready = []
    for vf in files:
        if id(vf) in not_ready:
            continue  # already reported with its save error
        if vf.dirty and not vf.stamp_only_dirty:  # the safety net: with the save above this should never fire
            errors.append(f"{vf.path.name}: has unsaved changes -- save or discard them first")
        else:
            ready.append(vf)

    def step(vf: VideoFile, _index: int) -> None:
        try:
            outcome = call_in_background(repair, str(vf.path), vf.check)
            fingerprint = call_in_background(video_fingerprint, str(vf.path))
        except Exception as exc:  # RepairError, or anything unexpected: keep going with the rest
            errors.append(f"{vf.path.name}: {exc}")
            return
        vf.load()  # fresh duration and technical details from the repaired file
        vf.record_check(outcome.check, fingerprint)
        vf._thumbnail_path = None
        repaired.append(vf)
        if outcome.note:
            notes.append(f"{vf.path.name}: {outcome.note}")

    run_with_progress(window, ready, step, "Repairing files...", threshold=1,
                      label_for=lambda vf: f"Repairing {vf.path.name}")
    repaired_ids = {id(vf) for vf in repaired}
    saved_and_repaired = [vf for vf in saved_first if id(vf) in repaired_ids]
    if saved_and_repaired:
        notes.insert(0, saved_first_note(len(saved_and_repaired)))
    return repaired, errors, notes


def saved_first_note(count: int) -> str:
    return f"Saved {count} file(s) with unsaved changes first, then repaired them."


def build_cut_confirmation(window, files: list[VideoFile]):
    """The confirmation before Cut Out Damaged Parts: exactly what will be lost, per file for a batch. Returns
    (message box, cut button, cancel button, heavy). A file where more than 25% of the duration would be lost makes it
    a strong warning with Cancel as the default button. Never run silently: the caller executes the box."""
    plans = [(vf, plan_cut(vf.check)) for vf in files]
    heavy = [(vf, plan) for vf, plan in plans if plan.heavy]
    if len(plans) == 1:
        body = plans[0][1].sentence()
    else:
        lines = [f"Cut out the damaged parts of {len(plans)} files? Per file:"]
        for vf, plan in plans:
            n = len(plan.spans)
            lines.append(
                f"- {vf.path.name}: {n} part{'s' if n != 1 else ''}, {plan.removed_seconds:.0f} s "
                f"({plan.fraction * 100:.0f}%): {plan.ranges_text(6)}"
            )
        lines.append("The rest of each file is kept unchanged. Each original goes to the Recycle Bin.")
        body = chr(10).join(lines)
    box = QMessageBox(window)
    box.setWindowTitle(CUT_BUTTON)
    if heavy:
        names = ", ".join(vf.path.name for vf, _plan in heavy)
        body = (
            f"WARNING: more than 25% of the length would be lost in: {names}." + chr(10) + chr(10) + body
            + chr(10) + chr(10) + "Check that this is what you want before going on."
        )
        box.setIcon(QMessageBox.Icon.Warning)
    else:
        box.setIcon(QMessageBox.Icon.Question)
    box.setText(body + chr(10) + chr(10) + "To undo, restore the original from the Recycle Bin.")
    cut_button = box.addButton(CUT_BUTTON, QMessageBox.ButtonRole.AcceptRole)
    cancel_button = box.addButton(QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(cancel_button if heavy else cut_button)
    box.setEscapeButton(cancel_button)
    return box, cut_button, cancel_button, bool(heavy)


def cut_files(window, files: list[VideoFile]) -> tuple[list[tuple[VideoFile, object]], list[str], bool]:
    """Cuts the damaged parts out of `files` (each already deep scanned): one counted progress dialog with Cancel;
    returns ([(file, CutOutcome)], errors, cancelled). Cancel leaves the file in progress untouched. Files with
    unsaved edits are skipped -- save or discard those first."""
    errors: list[str] = []
    ready = []
    for vf in files:
        if vf.dirty and not vf.stamp_only_dirty:
            errors.append(f"{vf.path.name}: has unsaved changes -- save or discard them first")
        else:
            ready.append(vf)
    per_file = 1000

    def work(progress):
        results: list[tuple[VideoFile, object, str, Optional[str]]] = []
        for index, vf in enumerate(ready):
            if progress.cancelled.is_set():
                break
            base = index * per_file
            name = vf.path.name

            def on_progress(done, total, base=base):
                progress.set_value(base + int(per_file * done / max(total, 1)))

            def on_status(message, name=name, index=index):
                progress.set_label(f"{name} ({index + 1} of {len(ready)}): {message}")

            on_status("starting")
            try:
                outcome = cut_out_damaged(str(vf.path), vf.check, cancel=progress.cancelled,
                                          progress=on_progress, status=on_status)
                fingerprint = video_fingerprint(str(vf.path))
                results.append((vf, outcome, fingerprint, None))
            except CutCancelled:
                break
            except Exception as exc:  # RepairError or anything unexpected: keep going with the rest
                results.append((vf, None, "", str(exc)))
            progress.set_value(base + per_file)
        return results

    results, cancelled = run_work_with_progress(
        window, max(len(ready), 1) * per_file, "Cutting out the damaged parts...", work, cancellable=True,
        show_after_ms=0, cancelling_text="Cancelling... (the original stays untouched)",
    )
    cut: list[tuple[VideoFile, object]] = []
    for vf, outcome, fingerprint, error in results:
        if error is not None:
            errors.append(f"{vf.path.name}: {error}")
            continue
        vf.load()  # fresh duration and technical details from the new file
        vf.record_check(outcome.check, fingerprint)
        vf._thumbnail_path = None
        cut.append((vf, outcome))
    return cut, errors, cancelled


def cut_report(cut: list[tuple[VideoFile, object]]) -> list[str]:
    """One line per file: the removed ranges, the new duration, the verify result."""
    from core.file_check import _format_seconds

    lines = []
    for vf, outcome in cut:
        ranges = ", ".join(f"{_format_seconds(s.start_s)}-{_format_seconds(s.end_s)}" for s in outcome.removed)
        n = len(outcome.removed)
        lines.append(
            f"{vf.path.name}: removed {n} part{'s' if n != 1 else ''} ({ranges}), {outcome.removed_seconds:.0f} s; "
            f"new duration {_format_seconds(outcome.new_duration)}; {outcome.verify}"
        )
    return lines


def _run_cut(window, targets: list[VideoFile], error_details) -> None:
    box, cut_button, _cancel, _heavy = build_cut_confirmation(window, targets)
    box.exec()
    if box.clickedButton() is not cut_button:
        return
    cut, errors, cancelled = cut_files(window, targets)
    window._refresh_table_rows()
    parts = [f"Cut damaged parts out of {len(cut)} of {len(targets)} file(s)"]
    if cancelled:
        parts.append("cancelled (the file being cut is untouched)")
    if errors:
        parts.append(f"{len(errors)} not cut")
    window.status_bar.showMessage(", ".join(parts))
    if errors:
        QMessageBox.warning(window, "Some files weren't cut", error_details(errors))
    if cut:
        QMessageBox.information(
            window, "Damaged parts cut out",
            error_details(cut_report(cut) + ["", "Each original is in the Recycle Bin; restore it from there to undo."]),
        )


def remux_confirmation(window, files: list[VideoFile], safety=None):
    """(box, remux_button, dry_run_box): the confirmation before a wrong-container remux. It lists every file and where
    the originals go, and carries the 'Dry run (report only)' choice, ticked by default."""
    safety = safety or library_safety.get_safety_settings()
    where = (
        f"the quarantine folder {safety.quarantine_root}" if safety.quarantine_root
        else "a 'remuxed originals <date>' folder beside each file"
    )
    box = QMessageBox(window)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle(TITLE)
    box.setText(
        f"Remux {len(files)} file(s) into the container their extension says?\n\n"
        "Each is rewritten losslessly (no re-encoding) to a temporary name, verified (real container, length, tracks, "
        f"a full decode with zero errors) and only then swapped in. The original is moved to {where}, never deleted, "
        "and a TSV log lets you undo it (command line: undo-remux). Files modified in the last "
        f"{safety.recent_minutes} minutes are skipped.\n\nThe files:\n"
        + "\n".join(f"  {vf.path}" for vf in files[:25]) + (f"\n  ... and {len(files) - 25} more" if len(files) > 25 else "")
    )
    box.setDetailedText("\n".join(str(vf.path) for vf in files))
    dry_box = QCheckBox("Dry run (report only): list what would be remuxed, write nothing")
    dry_box.setChecked(True)
    box.setCheckBox(dry_box)
    remux_button = box.addButton("Remux", QMessageBox.ButtonRole.AcceptRole)
    box.addButton(QMessageBox.StandardButton.Cancel)
    return box, remux_button, dry_box


def remux_files(window, files: list[VideoFile], dry_run: bool) -> tuple[list, str, bool]:
    """Runs container_remux.remux_file for each file under a cancellable progress dialog (Cancel kills ffmpeg).
    Returns (results, the log's path or "", cancelled). A file with unsaved edits is skipped, not remuxed."""
    safety = library_safety.get_safety_settings()
    cancel_event = threading.Event()
    results: list = []
    log = None if dry_run or not files else container_remux.RemuxLog(
        container_remux.default_log_path(str(files[0].path), safety))

    def step(vf: VideoFile, _index: int) -> None:
        if cancel_event.is_set():
            return
        if vf.dirty and not vf.stamp_only_dirty:
            results.append(container_remux.RemuxResult(
                str(vf.path), container_remux.SKIPPED, "it has unsaved edits: save them first"))
            return
        try:
            result = call_in_background(
                container_remux.remux_file, str(vf.path), dry_run=dry_run, safety=safety, log=log,
                cancel_event=cancel_event,
            )
        except Exception as exc:  # visible in the report, and the rest still run
            result = container_remux.RemuxResult(str(vf.path), container_remux.FAILED, f"the original is untouched: {exc}")
        results.append(result)
        if result.status == container_remux.REMUXED:
            vf.path = Path(result.new_path)
            vf.dirty = False
            vf.load()
            vf.check = None
            vf._thumbnail_path = None

    finished = run_with_progress(
        window, files, step, "Remuxing to the right container...", threshold=1, cancellable=True,
        label_for=lambda vf: f"Remuxing {vf.path.name}", on_cancel=cancel_event.set,
    )
    log_path = log.path if log is not None and os.path.isfile(log.path) else ""
    return results, log_path, cancel_event.is_set() or not finished


def _run_remux(window, targets: list[VideoFile], error_details) -> None:
    box, remux_button, dry_box = remux_confirmation(window, targets)
    box.exec()
    if box.clickedButton() is not remux_button:
        return
    dry_run = dry_box.isChecked()
    results, log_path, cancelled = remux_files(window, targets, dry_run)
    window._refresh_table_rows()
    done = [r for r in results if r.status in (container_remux.REMUXED, container_remux.WOULD)]
    failed = [r for r in results if r.status == container_remux.FAILED]
    skipped = [r for r in results if r.status == container_remux.SKIPPED]
    verb = "would be remuxed" if dry_run else "remuxed"
    parts = [("Dry run: " if dry_run else "") + f"{len(done)} of {len(targets)} file(s) {verb}"]
    if skipped:
        parts.append(f"{len(skipped)} skipped")
    if failed:
        parts.append(f"{len(failed)} failed")
    if cancelled:
        parts.append("cancelled (the file being remuxed is untouched)")
    summary = ", ".join(parts)
    window.status_bar.showMessage(summary)
    lines = [r.line() for r in results]
    if log_path:
        lines += ["", f"Undo log: {log_path}", f'Undo: videoredactor undo-remux "{log_path}"']
    if dry_run:
        lines += ["", "DRY RUN: nothing was changed or written."]
    report = QMessageBox(window)
    report.setIcon(QMessageBox.Icon.Warning if failed else QMessageBox.Icon.Information)
    report.setWindowTitle("Dry run" if dry_run else TITLE)
    report.setText(summary + ".")
    report.setDetailedText("\n".join(lines))
    report.setStandardButtons(QMessageBox.StandardButton.Ok)
    report.exec()


def run_check_and_repair(window, files: list[VideoFile], error_details, deep: Optional[bool] = None) -> None:
    """The whole Media > Check Files... flow. `error_details` formats
    a list of lines for a message box (the main window's helper). `deep` None asks (the remembered checkbox)."""
    if deep is None:
        deep = ask_check_options(window, len(files))
        if deep is None:
            return
    check_errors: list[str] = []
    checked = check_files(window, files, check_errors, deep=deep)
    window._refresh_table_rows()
    if check_errors:
        QMessageBox.warning(window, "Some files couldn't be checked", error_details(check_errors))
    if not checked:
        return
    dialog = FileCheckResultsDialog(checked, window)
    reported = sum(1 for vf in checked if vf.check.findings)
    window.status_bar.showMessage(f"Checked {len(checked)} file(s), {reported} with findings")
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return
    if dialog.action == "all":
        _run_fix_all(window, dialog.checked, error_details)
        return
    if dialog.action == "language":
        from gui.track_language_dialog import run_set_track_language

        run_set_track_language(window, dialog.language_files, error_details)
        return
    if dialog.action == "remux":
        if dialog.remuxable:
            _run_remux(window, dialog.remuxable, error_details)
        return
    if dialog.action == "cut":
        if dialog.cuttable:
            _run_cut(window, dialog.cuttable, error_details)
        return
    if not dialog.repairable:
        return
    targets = dialog.repairable
    answer = QMessageBox.question(
        window, "Repair Files",
        f"Repair {len(targets)} file(s)?\n\nEach is rewritten losslessly next to the original and "
        "checked; the original then goes to the Recycle Bin. A damaged file keeps everything "
        "readable -- the missing part can't be restored.",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes,
    )
    if answer != QMessageBox.StandardButton.Yes:
        return
    pending_before = unsaved(targets)
    repaired, errors, notes = repair_files(window, targets)
    window._refresh_table_rows()
    saved_first = [vf for vf in pending_before if vf in repaired and not unsaved([vf])]
    if saved_first:
        parts = [f"Saved {len(saved_first)} file(s) with unsaved changes first, then repaired "
                 f"{len(repaired)} of {len(targets)} file(s)"]
    else:
        parts = [f"Repaired {len(repaired)} of {len(targets)} file(s)"]
    if errors:
        parts.append(f"{len(errors)} not repaired")
    window.status_bar.showMessage(", ".join(parts))
    if errors:
        QMessageBox.warning(window, "Some files weren't repaired", error_details(errors))
    if notes:
        QMessageBox.information(window, "Repaired, with notes", error_details(notes))


FIX_ALL_STEP_TEXT = {
    "remux": "remux into the container the extension says (lossless, verified; the original is moved to the "
             "'remuxed originals' folder with an undo log)",
    "cut": "cut out the zero-filled parts (lossless; the rest is kept; the original goes to the Recycle Bin)",
    "repair": "repair (seek index, the MP4's index moved to the front, duration; lossless; the original goes to "
              "the Recycle Bin)",
    "language": "set the language of the audio/subtitle tracks that have none (a short dialog asks which language)",
}


def _fix_all_plan(files: list[VideoFile]) -> tuple[dict[str, list[VideoFile]], list[str]]:
    """What Fix All will run: the eligible files per step, minus the cut of any file that would lose more than 25% of
    its length (that one stays a deliberate decision of the Cut button). Also returns the notes to show."""
    fixes = eligible_fixes(files)
    notes: list[str] = []
    heavy = [vf for vf in fixes["cut"] if plan_cut(vf.check).heavy]
    if heavy:
        fixes["cut"] = [vf for vf in fixes["cut"] if vf not in heavy]
        notes.append("Not cut by Fix All (more than 25% of the length would be lost; use Cut Out Damaged Parts "
                     "yourself after looking at it): " + ", ".join(vf.path.name for vf in heavy))
    return fixes, notes


def _run_fix_all(window, files: list[VideoFile], error_details) -> None:
    """Fix All: every step that applies, in a safe order (remux, cut, repair, language), one confirmation. After a
    step that replaces a file, the files it touched are checked again, so the next step works from what is on disk now
    and not from the old findings. The language step is last because it asks which language. Nothing is skipped
    silently: one report lists what each step did."""
    fixes, plan_notes = _fix_all_plan(files)
    steps = [step for step in FIX_ORDER if fixes[step]]
    if not steps:
        QMessageBox.information(window, "Fix All", "Nothing to fix.\n\n" + "\n".join(plan_notes))
        return
    lines = [f"{index + 1}. {len(fixes[step])} file(s): {FIX_ALL_STEP_TEXT[step]}" for index, step in enumerate(steps)]
    answer = QMessageBox.question(
        window, "Fix All",
        "Fix everything that can be fixed, in this order?\n\n" + "\n".join(lines)
        + ("\n\n" + "\n".join(plan_notes) if plan_notes else "")
        + "\n\nFiles with unsaved edits are saved first or skipped, as with each single fix.",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes,
    )
    if answer != QMessageBox.StandardButton.Yes:
        return

    report: list[str] = list(plan_notes)
    errors: list[str] = []
    check_errors: list[str] = []

    if "remux" in steps:
        results, _log, _cancelled = remux_files(window, fixes["remux"], dry_run=False)
        report += [r.line() for r in results]
        errors += [r.line() for r in results if r.status in (container_remux.FAILED, container_remux.SKIPPED)]
        # a remuxed file lost its check result: look at it again so the next steps see what is on disk now
        again = [vf for vf in fixes["remux"] if vf.check is None and vf.path.exists()]
        if again:
            check_files(window, again, check_errors, deep=False)
    if "cut" in steps:
        current = [vf for vf in eligible_fixes(files)["cut"] if vf in fixes["cut"]]
        cut, cut_errors, _cancelled = cut_files(window, current)
        report += cut_report(cut)
        errors += cut_errors
    if "repair" in steps:
        current = eligible_fixes(files)["repair"]
        repaired, repair_errors, repair_notes = repair_files(window, current)
        report += [f"{vf.path.name}: repaired" for vf in repaired] + repair_notes
        errors += repair_errors
    window._refresh_table_rows()

    language_files = eligible_fixes(files)["language"] if "language" in steps else []
    parts = [f"Fix All: {len([s for s in steps if s != 'language'])} step(s) run"]
    if errors:
        parts.append(f"{len(errors)} problem(s)")
    window.status_bar.showMessage(", ".join(parts))
    if report or errors:
        box = QMessageBox(window)
        box.setIcon(QMessageBox.Icon.Warning if errors else QMessageBox.Icon.Information)
        box.setWindowTitle("Fix All")
        box.setText(", ".join(parts) + (". The track languages come next." if language_files else "."))
        detail = list(report)
        if errors:
            detail += ["", "PROBLEMS:"] + errors
        if check_errors:
            detail += ["", "Could not be re-checked:"] + check_errors
        box.setDetailedText("\n".join(detail))
        box.exec()
    if language_files:
        from gui.track_language_dialog import run_set_track_language

        run_set_track_language(window, language_files, error_details)
