"""
SearchReplaceDialog: Operations > Search/Replace... -- batch find-and-
replace within a chosen text field across the selected files (or all
loaded files, if nothing's selected -- same convention as every other
batch tool in this project).

Plain substring replace, not regex -- see core/text_transforms.py's
module docstring for why. Stages changes onto vf.metadata and marks
vf.dirty = True, same "Apply doesn't mean Save" principle as the other
new batch tools.

NOTE: not runnable in this sandbox -- no PyQt6. Syntax-checked and
reviewed only.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QComboBox, QLabel, QPushButton,
    QLineEdit, QCheckBox, QTableWidget, QTableWidgetItem,
)

from core.video_metadata import TEXT_FIELDS
from core.text_transforms import apply_search_replace
from gui.tag_panel import FIELD_LABELS


class SearchReplaceDialog(QDialog):
    def __init__(self, video_files: list, parent=None):
        super().__init__(parent)
        self.video_files = video_files
        self.replaced_count = 0

        self.setWindowTitle(f"Search/Replace ({len(video_files)} file(s))")
        self.resize(700, 500)

        layout = QVBoxLayout(self)

        field_row = QHBoxLayout()
        field_row.addWidget(QLabel("Field:"))
        self.field_combo = QComboBox()
        for field_name in TEXT_FIELDS:
            self.field_combo.addItem(FIELD_LABELS.get(field_name, field_name), field_name)
        self.field_combo.currentIndexChanged.connect(self._update_preview)
        field_row.addWidget(self.field_combo)
        field_row.addStretch()
        layout.addLayout(field_row)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Find:"))
        self.search_edit = QLineEdit()
        self.search_edit.textChanged.connect(self._update_preview)
        search_row.addWidget(self.search_edit)
        layout.addLayout(search_row)

        replace_row = QHBoxLayout()
        replace_row.addWidget(QLabel("Replace with:"))
        self.replace_edit = QLineEdit()
        self.replace_edit.textChanged.connect(self._update_preview)
        replace_row.addWidget(self.replace_edit)
        layout.addLayout(replace_row)

        self.case_sensitive_check = QCheckBox("Case sensitive")
        self.case_sensitive_check.setChecked(True)
        self.case_sensitive_check.stateChanged.connect(self._update_preview)
        layout.addWidget(self.case_sensitive_check)

        self.preview_table = QTableWidget()
        self.preview_table.setColumnCount(3)
        self.preview_table.setHorizontalHeaderLabels(["Filename", "Current Value", "New Value"])
        layout.addWidget(self.preview_table)

        button_row = QHBoxLayout()
        button_row.addStretch()
        self.apply_button = QPushButton("Apply to Selected Files")
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self._on_apply)
        button_row.addWidget(self.apply_button)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        button_row.addWidget(self.cancel_button)
        layout.addLayout(button_row)

        self._update_preview()

    def _current_field(self) -> str:
        return self.field_combo.currentData()

    def _update_preview(self) -> None:
        field_name = self._current_field()
        search = self.search_edit.text()
        replace = self.replace_edit.text()
        case_sensitive = self.case_sensitive_check.isChecked()

        self.preview_table.setRowCount(len(self.video_files))
        any_change = False

        for row, vf in enumerate(self.video_files):
            current_value = getattr(vf.metadata, field_name, "") or ""
            new_value = apply_search_replace(current_value, search, replace, case_sensitive)
            self.preview_table.setItem(row, 0, QTableWidgetItem(vf.path.name))
            self.preview_table.setItem(row, 1, QTableWidgetItem(current_value))
            self.preview_table.setItem(row, 2, QTableWidgetItem(new_value))
            if new_value != current_value:
                any_change = True

        self.apply_button.setEnabled(bool(search) and any_change)

    def _on_apply(self) -> None:
        field_name = self._current_field()
        search = self.search_edit.text()
        replace = self.replace_edit.text()
        case_sensitive = self.case_sensitive_check.isChecked()

        count = 0
        for vf in self.video_files:
            current_value = getattr(vf.metadata, field_name, "") or ""
            new_value = apply_search_replace(current_value, search, replace, case_sensitive)
            if new_value == current_value:
                continue
            setattr(vf.metadata, field_name, new_value)
            vf.dirty = True
            count += 1
        self.replaced_count = count
        self.accept()
