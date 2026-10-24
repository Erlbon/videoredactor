"""
gui/join_files_dialog.py

Media > Join Files (and right-click > Fix and Convert > Join Files): joins the parts of a movie that is split over several
files into one file without re-encoding (core/join_files.py). The dialog lists each movie the selected files make up with
its parts in order, whether they can be joined (and why not), and the joined length; a movie that cannot be joined is shown
and cannot be ticked. The tags and the poster come from the first part. Every part is moved (never deleted) to a
"joined originals" folder with an undo log.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QLabel, QMessageBox, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)
from redactor_common.gui.progress import run_with_progress
from redactor_common.gui.sortable_table import natural_key
from redactor_common.gui.work_progress import run_work_with_progress

from core import join_files
from core.video_file import VideoFile
from redactor_common.gui.owned_dialog import OwnedDialog

TITLE = "Join Files"
OK_TINT = ("#EAF3DE", "#27500A", "#27500A", "#C0DD97")    # light bg, light text, dark bg, dark text
BAD_TINT = ("#FCEBEB", "#791F1F", "#791F1F", "#F7C1C1")
HEADERS = ["Movie / part", "Length", "Compatible", "Note"]


def _minutes(seconds: Optional[float]) -> str:
    if seconds is None:
        return ""
    total = int(round(seconds))
    return f"{total // 3600}:{total % 3600 // 60:02d}:{total % 60:02d}" if total >= 3600 else f"{total // 60}:{total % 60:02d}"


class _SortItem(QTreeWidgetItem):
    """A tree row that sorts naturally on the text of the clicked column."""

    def __lt__(self, other) -> bool:
        column = self.treeWidget().sortColumn() if self.treeWidget() is not None else 0
        return natural_key(self.text(column)) < natural_key(other.text(column))


class JoinFilesDialog(OwnedDialog):
    """`plans`: the checked groups. After exec(): `chosen` are the plans ticked, `chapters` and `dry_run` the options."""

    def __init__(self, plans: list[join_files.JoinPlan], parent=None):
        super().__init__(parent)
        self.setWindowTitle(TITLE)
        self.resize(900, 460)
        self.plans = plans
        self.chosen: list[join_files.JoinPlan] = []
        layout = QVBoxLayout(self)
        note = QLabel(
            "Join the parts of each movie into one file. Nothing is re-encoded; the tags and the poster come from the "
            "first part. Every part is moved (never deleted) to a \"joined originals\" folder, and an undo log lets you "
            "put them back. A movie whose parts differ (codec, size, frame rate, tracks) cannot be joined without "
            "re-encoding and is left alone."
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(len(HEADERS))
        self.tree.setHeaderLabels(HEADERS)
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(False)
        self.tree.setWordWrap(True)
        self.tree.setSortingEnabled(True)
        self.tree.header().setSectionsClickable(True)
        self.tree.header().setStretchLastSection(True)
        self.tree.setColumnWidth(0, 360)
        self.tree.setColumnWidth(1, 80)
        self.tree.setColumnWidth(2, 90)
        dark = self.palette().window().color().lightness() < 128
        self._ok = self._brushes(OK_TINT, dark)
        self._bad = self._brushes(BAD_TINT, dark)
        self.tree.setSortingEnabled(False)
        for plan in plans:
            self.tree.addTopLevelItem(self._group_item(plan))
        self.tree.expandAll()
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.tree.itemChanged.connect(lambda *_: self._update_button())
        layout.addWidget(self.tree, 1)

        self.chapters_box = QCheckBox("Add a chapter at each part (Part 1, Part 2, ...)")
        self.dry_run_box = QCheckBox("Dry run (report only): list what would be joined, write nothing")
        layout.addWidget(self.chapters_box)
        layout.addWidget(self.dry_run_box)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.join_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._update_button()

    @staticmethod
    def _brushes(tint, dark: bool) -> tuple[QBrush, QBrush]:
        bg, fg = (tint[2], tint[3]) if dark else (tint[0], tint[1])
        return QBrush(QColor(bg)), QBrush(QColor(fg))

    def _group_item(self, plan: join_files.JoinPlan) -> QTreeWidgetItem:
        group = plan.group
        item = _SortItem([f"{os.path.basename(group.output_path)} ({len(group.parts)} part{'s' if len(group.parts) != 1 else ''})",
                          _minutes(plan.total_seconds), "yes" if plan.compatible else "no",
                          plan.summary if plan.compatible else plan.reason])
        item.setData(0, Qt.ItemDataRole.UserRole, plan)
        font = item.font(0)
        font.setBold(True)
        item.setFont(0, font)
        if plan.compatible:
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Checked)
        else:
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
        brush, text = self._ok if plan.compatible else self._bad
        item.setBackground(2, brush)
        item.setForeground(2, text)
        for index, path in enumerate(group.parts):
            number = group.numbers[index]
            length = plan.durations[index] if index < len(plan.durations) else None
            child = _SortItem([("  " if number is None else f"  {number}.  ") + os.path.basename(path), _minutes(length), "", ""])
            child.setFlags(child.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            item.addChild(child)
        item.setToolTip(3, item.text(3))
        return item

    def _ticked(self) -> list[join_files.JoinPlan]:
        out = []
        for row in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(row)
            if item.checkState(0) == Qt.CheckState.Checked:
                out.append(item.data(0, Qt.ItemDataRole.UserRole))
        return out

    def _update_button(self) -> None:
        count = len(self._ticked())
        self.join_button.setText(f"Join {count} movie{'s' if count != 1 else ''}" if count else "Join")
        self.join_button.setEnabled(count > 0)

    def _on_accept(self) -> None:
        self.chosen = self._ticked()
        self.accept()

    @property
    def chapters(self) -> bool:
        return self.chapters_box.isChecked()

    @property
    def dry_run(self) -> bool:
        return self.dry_run_box.isChecked()


def check_groups(window, files: list[VideoFile]) -> list[join_files.JoinPlan]:
    """Groups the files and checks each group (ffprobe per part) under a progress dialog."""
    groups = join_files.group_files([str(vf.path) for vf in files])
    plans: list[join_files.JoinPlan] = []

    def step(group, _index: int) -> None:
        plans.append(join_files.check_group(group))

    run_with_progress(window, groups, step, "Checking the parts...", threshold=1,
                      label_for=lambda g: f"Checking {os.path.basename(g.output_path)}")
    plans.sort(key=lambda p: natural_key(p.group.output_path))
    return plans


def join_chosen(window, plans: list[join_files.JoinPlan], chapters: bool, dry_run: bool):
    """Joins the ticked groups under a cancellable progress dialog; returns (results, the log's path, cancelled)."""
    def work(progress):
        results: list[join_files.JoinResult] = []
        log_path = ""
        for index, plan in enumerate(plans):
            if progress.cancelled.is_set():
                break
            progress.set_label(f"Joining {os.path.basename(plan.group.output_path)} ({index + 1} of {len(plans)})")
            part, log_path = join_files.join_groups(
                [plan.group], dry_run=dry_run, chapters=chapters, plans=[plan], cancel_event=progress.cancelled,
                log_path=log_path,
            )
            results += part
            progress.set_value((index + 1) * 1000)
        return results, log_path

    (results, log_path), cancelled = run_work_with_progress(
        window, max(len(plans), 1) * 1000, "Joining the parts...", work, cancellable=True, show_after_ms=0,
        cancelling_text="Cancelling... (the file being joined is untouched)",
    )
    return results, log_path, cancelled


def apply_to_list(window, files: list[VideoFile], plans: list[join_files.JoinPlan], results) -> None:
    """The window's list after a real join: the first part's entry becomes the joined file, the other parts leave the list."""
    by_path = {str(vf.path): vf for vf in files}
    drop: set[int] = set()
    for plan, result in zip(plans, results):
        if result.status != join_files.JOINED:
            continue
        first = by_path.get(plan.group.parts[0])
        if first is not None:
            first.path = Path(result.new_path)
            first.dirty = False
            first.load()
            first.check = None
            first._thumbnail_path = None
        for path in plan.group.parts[1:]:
            part = by_path.get(path)
            if part is not None:
                drop.add(id(part))
    if drop:
        window.video_files = [vf for vf in window.video_files if id(vf) not in drop]
    window._clear_undo()
    window._refresh_table_rows()


def run_join_files(window, files: list[VideoFile], error_details) -> None:
    """The whole Join Files flow for `files` (the selected ones). Unsaved edits are saved first, like a repair."""
    from gui.file_check_dialog import save_pending_first

    files = [vf for vf in files if not vf.load_error]
    if len(files) < 2:
        QMessageBox.information(window, TITLE, "Select two or more files that are parts of one movie "
                                               "(for example Title cd1.mp4 and Title cd2.mp4).")
        return
    _saved, save_errors, not_ready = save_pending_first(window, files)
    if save_errors:
        QMessageBox.warning(window, TITLE, error_details(save_errors))
    files = [vf for vf in files if id(vf) not in not_ready]
    plans = check_groups(window, files)
    if not plans:
        QMessageBox.information(window, TITLE, "Nothing to join.")
        return
    dialog = JoinFilesDialog(plans, window)
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    chosen, chapters, dry_run = dialog.chosen, dialog.chapters, dialog.dry_run   # read now: the dialog is freed once control is back in the event loop
    if not accepted or not chosen:
        return
    results, log_path, cancelled = join_chosen(window, chosen, chapters, dry_run)
    if not dry_run:
        apply_to_list(window, files, chosen, results)
    done = [r for r in results if r.status in (join_files.JOINED, join_files.WOULD)]
    bad = [r for r in results if r.status in (join_files.FAILED, join_files.SKIPPED)]
    verb = "would be joined" if dry_run else "joined"
    parts = [("Dry run: " if dry_run else "") + f"{len(done)} of {len(chosen)} movie(s) {verb}"]
    if bad:
        parts.append(f"{len(bad)} not joined")
    if cancelled:
        parts.append("cancelled (the file being joined is untouched)")
    summary = ", ".join(parts)
    window.status_bar.showMessage(summary)
    lines = [r.line() for r in results]
    if log_path:
        lines += ["", f"Undo log: {log_path}", f'Undo: videoredactor undo-join "{log_path}"']
    if dry_run:
        lines += ["", "DRY RUN: nothing was changed or written."]
    box = QMessageBox(window)
    box.setIcon(QMessageBox.Icon.Warning if bad else QMessageBox.Icon.Information)
    box.setWindowTitle("Dry run" if dry_run else TITLE)
    box.setText(summary + ".")
    box.setDetailedText("\n".join(lines))
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    box.exec()
