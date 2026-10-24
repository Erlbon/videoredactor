"""
gui/studios_dialog.py

Tools > Studios: the table that gives each studio its website. A file's Website column is derived from its Studio through
this table, so an address is entered once. Both columns are editable; a row with no studio or no usable address is
dropped when the dialog is accepted. Headers sort and resize.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout,
)
from redactor_common.gui.sortable_table import enable_sorting, suspend_sorting

from core import studios
from redactor_common.gui.owned_dialog import OwnedDialog


class StudiosDialog(OwnedDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Studios")
        self.resize(560, 460)
        self.changed = False

        layout = QVBoxLayout(self)
        note = QLabel(
            "Each studio's website. The Website column and the panel line are taken from a file's Studio through this "
            "table. Applied ThePornDB and StashDB lookups add a studio's site here when it is missing; what you type "
            "is never overwritten. Double-click a cell to edit it."
        )
        note.setWordWrap(True)
        layout.addWidget(note)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Studio", "Website"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 240)
        enable_sorting(self.table)
        layout.addWidget(self.table, 1)
        with suspend_sorting(self.table):
            for name, host in studios.load_studios().items():
                self._add_row(name, host)

        row = QHBoxLayout()
        add = QPushButton("Add Studio")
        add.clicked.connect(self._on_add)
        remove = QPushButton("Remove Selected")
        remove.clicked.connect(self._on_remove)
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch(1)
        layout.addLayout(row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _add_row(self, name: str = "", host: str = "") -> int:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(name))
        self.table.setItem(row, 1, QTableWidgetItem(host))
        return row

    def _on_add(self) -> None:
        with suspend_sorting(self.table):
            row = self._add_row()
        self.table.editItem(self.table.item(row, 0))

    def _on_remove(self) -> None:
        for row in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(row)

    def entries(self) -> dict[str, str]:
        """The table as {studio: website host}; rows without a studio or a usable address are left out."""
        out: dict[str, str] = {}
        for row in range(self.table.rowCount()):
            name = (self.table.item(row, 0).text() if self.table.item(row, 0) else "").strip()
            host = studios.normalize_website(self.table.item(row, 1).text() if self.table.item(row, 1) else "")
            if name and host:
                out[name] = host
        return out

    def _on_accept(self) -> None:
        entries = self.entries()
        skipped = self.table.rowCount() - len(entries)
        before = studios.load_studios()
        studios.save_studios(entries)
        self.changed = entries != before
        if skipped:
            QMessageBox.information(
                self, "Studios", f"{skipped} row(s) had no studio name or no usable website address and were left out.")
        self.accept()
