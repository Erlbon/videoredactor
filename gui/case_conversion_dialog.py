"""
CaseConversionDialog: Operations > Case Conversion... -- batch-convert
the case (UPPER, lower, Title Case, Sentence case) of a chosen text
field across the selected files (or all loaded files, if nothing's
selected -- same convention RenameByPatternDialog and
ParseFilenameDialog already use).

Stages changes onto vf.metadata and marks vf.dirty = True -- same
"Apply doesn't mean Save" principle as every other batch metadata tool
in this project. Actual case-conversion logic lives in
core/text_transforms.py, kept separate specifically so it's testable
without PyQt6.

NOTE: not runnable in this sandbox -- no PyQt6. Syntax-checked and
reviewed only.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QComboBox, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem,
)

from core.video_metadata import TEXT_FIELDS
from core.text_transforms import apply_case_conversion, CASE_MODES
from gui.tag_panel import FIELD_LABELS

CASE_MODE_LABELS = {
    "upper": "UPPERCASE", "lower": "lowercase",
    "title": "Title Case", "sentence": "Sentence case",
}


class CaseConversionDialog(QDialog):
    def __init__(self, video_files: list, parent=None):
        super().__init__(parent)
        self.video_files = video_files
        self.converted_count = 0

        self.setWindowTitle(f"Case Conversion ({len(video_files)} file(s))")
        self.resize(700, 500)

        layout = QVBoxLayout(self)

        controls_row = QHBoxLayout()
        controls_row.addWidget(QLabel("Field:"))
        self.field_combo = QComboBox()
        for field_name in TEXT_FIELDS:
            self.field_combo.addItem(FIELD_LABELS.get(field_name, field_name), field_name)
        self.field_combo.currentIndexChanged.connect(self._update_preview)
        controls_row.addWidget(self.field_combo)

        controls_row.addWidget(QLabel("Convert to:"))
        self.mode_combo = QComboBox()
        for mode in CASE_MODES:
            self.mode_combo.addItem(CASE_MODE_LABELS[mode], mode)
        self.mode_combo.currentIndexChanged.connect(self._update_preview)
        controls_row.addWidget(self.mode_combo)
        controls_row.addStretch()
        layout.addLayout(controls_row)

        self.preview_table = QTableWidget()
        self.preview_table.setColumnCount(3)
        self.preview_table.setHorizontalHeaderLabels(["Filename", "Current Value", "New Value"])
        layout.addWidget(self.preview_table)

        button_row = QHBoxLayout()
        button_row.addStretch()
        self.apply_button = QPushButton("Apply to Selected Files")
        self.apply_button.clicked.connect(self._on_apply)
        button_row.addWidget(self.apply_button)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        button_row.addWidget(self.cancel_button)
        layout.addLayout(button_row)

        self._update_preview()

    def _current_field(self) -> str:
        return self.field_combo.currentData()

    def _current_mode(self) -> str:
        return self.mode_combo.currentData()

    def _update_preview(self) -> None:
        field_name = self._current_field()
        mode = self._current_mode()
        self.preview_table.setRowCount(len(self.video_files))

        for row, vf in enumerate(self.video_files):
            current_value = getattr(vf.metadata, field_name, "") or ""
            new_value = apply_case_conversion(current_value, mode)
            self.preview_table.setItem(row, 0, QTableWidgetItem(vf.path.name))
            self.preview_table.setItem(row, 1, QTableWidgetItem(current_value))
            self.preview_table.setItem(row, 2, QTableWidgetItem(new_value))

    def _on_apply(self) -> None:
        field_name = self._current_field()
        mode = self._current_mode()
        count = 0
        for vf in self.video_files:
            current_value = getattr(vf.metadata, field_name, "") or ""
            if not current_value:
                continue  # nothing to convert -- don't mark dirty over a no-op
            new_value = apply_case_conversion(current_value, mode)
            if new_value == current_value:
                continue  # already in the target case -- same no-op reasoning
            setattr(vf.metadata, field_name, new_value)
            vf.dirty = True
            count += 1
        self.converted_count = count
        self.accept()
