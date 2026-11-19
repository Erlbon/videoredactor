"""
gui/convert_preview_dialog.py

The dry run of Media > Convert as a table (decision 12B, 2026-11-18): one row per file with a tick, the route (Remux,
Shrink, Re-encode, Skip), resolution, codec, video Mb/s and the action. Sortable; "Only files that would change"
hides the skipped rows. "Convert the ticked files" closes the window with the ticked paths in `ticked_paths`; the
caller runs the real conversion for them with the same choices. The rows come from core/convert_preview.py.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialogButtonBox, QHeaderView, QLabel, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from core.convert_preview import PreviewRow
from redactor_common.gui.owned_dialog import OwnedDialog
from redactor_common.gui.sortable_table import NumericTableWidgetItem, suspend_sorting

COLUMNS = ("File", "Route", "Resolution", "Codec", "Video Mb/s", "Action")
PATH_ROLE = Qt.ItemDataRole.UserRole


class ConvertPreviewDialog(OwnedDialog):
    """After exec() == Accepted: `ticked_paths` are the files to convert for real."""

    def __init__(self, rows: list[PreviewRow], summary: str, report: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Dry run: nothing was changed")
        self.resize(1000, 520)
        self._rows = rows
        self.ticked_paths: list[str] = []
        layout = QVBoxLayout(self)
        head = QLabel(summary)
        head.setWordWrap(True)
        layout.addWidget(head)

        self.table = QTableWidget(len(rows), len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        with suspend_sorting(self.table):
            for r, row in enumerate(rows):
                name = QTableWidgetItem(row.name)
                name.setData(PATH_ROLE, row.path)
                name.setToolTip(row.path)
                name.setFlags(name.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                name.setCheckState(Qt.CheckState.Checked if row.changes else Qt.CheckState.Unchecked)
                if not row.changes:
                    name.setFlags(name.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)  # nothing to convert
                self.table.setItem(r, 0, name)
                self.table.setItem(r, 1, QTableWidgetItem(row.route))
                height = row.resolution[:-1] if row.resolution.endswith("p") else ""
                self.table.setItem(r, 2, NumericTableWidgetItem(row.resolution, sort_value=float(height) if height else None))
                self.table.setItem(r, 3, QTableWidgetItem(row.codec))
                mbps = f"{row.mbps:.1f}" if row.mbps is not None else ""
                self.table.setItem(r, 4, NumericTableWidgetItem(mbps, sort_value=row.mbps))
                action = QTableWidgetItem(row.action)
                action.setToolTip(row.action)
                self.table.setItem(r, 5, action)
        # The files' own order until a header is clicked (sorting on would otherwise sort by the first column at once).
        header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        self.table.setSortingEnabled(True)
        self.table.resizeColumnsToContents()
        if self.table.columnWidth(0) > 380:
            self.table.setColumnWidth(0, 380)
        layout.addWidget(self.table, 1)

        self.only_changes = QCheckBox("Only files that would change")
        self.only_changes.toggled.connect(self._filter)
        layout.addWidget(self.only_changes)
        self._report = report

        buttons = QDialogButtonBox()
        self.convert_button = QPushButton("Convert the ticked files")
        self.convert_button.clicked.connect(self._convert)
        buttons.addButton(self.convert_button, QDialogButtonBox.ButtonRole.AcceptRole)
        copy = QPushButton("Copy the report")
        copy.setToolTip("Copies every file's dry-run line, as text")
        copy.clicked.connect(self._copy_report)
        buttons.addButton(copy, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.table.itemChanged.connect(lambda _item: self._update_button())
        self._update_button()

    def _filter(self, only: bool) -> None:
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            changes = bool(item.flags() & Qt.ItemFlag.ItemIsUserCheckable)
            self.table.setRowHidden(r, only and not changes)

    def _checked(self) -> list[str]:
        paths = []
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item.checkState() == Qt.CheckState.Checked and item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                paths.append(item.data(PATH_ROLE))
        return paths

    def _update_button(self) -> None:
        count = len(self._checked())
        self.convert_button.setText(f"Convert the ticked files ({count})")
        self.convert_button.setEnabled(count > 0)

    def _convert(self) -> None:
        self.ticked_paths = self._checked()
        if self.ticked_paths:
            self.accept()

    def _copy_report(self) -> None:
        from PyQt6.QtWidgets import QApplication  # noqa: PLC0415

        QApplication.clipboard().setText(self._report)
