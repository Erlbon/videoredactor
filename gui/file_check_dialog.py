"""
gui/file_check_dialog.py

Operations > Check Files... (core/file_check.py): the quick check for
the selected files (or all loaded ones), then a results dialog listing
every file with something to report, and Repair for those a lossless
remux can fix -- each original goes to the Recycle Bin.

Each file is checked/repaired on a worker thread
(redactor_common's call_in_background), so the window keeps painting
while ffmpeg reads a multi-GB file; Cancel takes effect between files.
Results land on VideoFile.check and show in the Status column
(DAMAGED / REPAIRABLE / NOTE / CHECKED OK, the findings as a tooltip).
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)
from redactor_common.gui.background_call import call_in_background
from redactor_common.gui.progress import run_with_progress

from core.file_check import quick_check, repair
from core.video_file import VideoFile

TITLE = "Check Files"


class FileCheckResultsDialog(QDialog):
    """The files with findings, worst first; "Repair" accepts the dialog."""

    def __init__(self, checked: list[VideoFile], parent=None):
        super().__init__(parent)
        self.setWindowTitle(TITLE)
        self.resize(900, 420)
        order = {"DAMAGED": 0, "REPAIRABLE": 1, "NOTE": 2, "CHECKED OK": 3}
        reported = sorted(
            (vf for vf in checked if vf.check and vf.check.findings),
            key=lambda vf: (order.get(vf.check.status, 9), vf.path.name.lower()),
        )
        self.repairable = [vf for vf in checked if vf.check and vf.check.can_repair]
        counts = {status: sum(1 for vf in checked if vf.check and vf.check.status == status) for status in order}

        layout = QVBoxLayout(self)
        summary = QLabel(
            f"Checked {len(checked)} file(s): {counts['CHECKED OK']} OK, {counts['DAMAGED']} damaged, "
            f"{counts['REPAIRABLE']} repairable, {counts['NOTE']} with notes only.<br>"
            "<b>Repair</b> rewrites a file losslessly (no re-encoding): it rebuilds a missing seek "
            "index or duration, moves an MP4's index to the front, fixes default audio tracks, and "
            "for a damaged file keeps everything readable -- what's missing can't be restored. "
            "Tags and cover are kept; each original goes to the Recycle Bin."
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)

        self.table = QTableWidget(len(reported), 3)
        self.table.setHorizontalHeaderLabels(["File", "Status", "Findings"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 280)
        for row, vf in enumerate(reported):
            for col, text in enumerate((vf.path.name, vf.check.status, vf.check.summary())):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                self.table.setItem(row, col, item)
        layout.addWidget(self.table)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.repair_button: Optional[QPushButton] = None
        if self.repairable:
            self.repair_button = buttons.addButton(
                f"Repair {len(self.repairable)} File(s)...", QDialogButtonBox.ButtonRole.AcceptRole
            )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


def check_files(window, files: list[VideoFile], errors: Optional[list[str]] = None) -> list[VideoFile]:
    """Runs the quick check on `files`; returns the ones checked (fewer
    if cancelled). A file whose check blows up (OSError, a timeout...) is
    appended to `errors` and the rest still run."""
    checked: list[VideoFile] = []

    def step(vf: VideoFile, _index: int) -> None:
        try:
            vf.check = call_in_background(quick_check, str(vf.path))
        except Exception as exc:
            if errors is not None:
                errors.append(f"{vf.path.name}: {exc}")
            return
        checked.append(vf)

    run_with_progress(window, files, step, "Checking files...", threshold=1,
                      label_for=lambda vf: f"Checking {vf.path.name}")
    return checked


def repair_files(window, files: list[VideoFile]) -> tuple[list[VideoFile], list[str], list[str]]:
    """Repairs `files` (each already checked and repairable); returns
    (repaired, errors, notes). Files with unsaved edits are skipped --
    save or discard those first."""
    repaired: list[VideoFile] = []
    errors: list[str] = []
    notes: list[str] = []
    ready = []
    for vf in files:
        if vf.dirty:
            errors.append(f"{vf.path.name}: has unsaved changes -- save or discard them first")
        else:
            ready.append(vf)

    def step(vf: VideoFile, _index: int) -> None:
        try:
            outcome = call_in_background(repair, str(vf.path), vf.check)
        except Exception as exc:  # RepairError, or anything unexpected: keep going with the rest
            errors.append(f"{vf.path.name}: {exc}")
            return
        vf.load()  # fresh duration and technical details from the repaired file
        vf.check = outcome.check
        vf._thumbnail_path = None
        repaired.append(vf)
        if outcome.note:
            notes.append(f"{vf.path.name}: {outcome.note}")

    run_with_progress(window, ready, step, "Repairing files...", threshold=1,
                      label_for=lambda vf: f"Repairing {vf.path.name}")
    return repaired, errors, notes


def run_check_and_repair(window, files: list[VideoFile], error_details) -> None:
    """The whole Operations > Check Files... flow. `error_details` formats
    a list of lines for a message box (the main window's helper)."""
    check_errors: list[str] = []
    checked = check_files(window, files, check_errors)
    window._refresh_table_rows()
    if check_errors:
        QMessageBox.warning(window, "Some files couldn't be checked", error_details(check_errors))
    if not checked:
        return
    dialog = FileCheckResultsDialog(checked, window)
    reported = sum(1 for vf in checked if vf.check.findings)
    window.status_bar.showMessage(f"Checked {len(checked)} file(s), {reported} with findings")
    if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.repairable:
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
    repaired, errors, notes = repair_files(window, targets)
    window._refresh_table_rows()
    parts = [f"Repaired {len(repaired)} of {len(targets)} file(s)"]
    if errors:
        parts.append(f"{len(errors)} not repaired")
    window.status_bar.showMessage(", ".join(parts))
    if errors:
        QMessageBox.warning(window, "Some files weren't repaired", error_details(errors))
    if notes:
        QMessageBox.information(window, "Repaired, with notes", error_details(notes))
