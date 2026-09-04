"""
AutoNumberingDialog: Operations > Auto-Numbering... -- batch-assigns a
sequential number to a chosen field across the selected files (or all
loaded files, if nothing's selected -- same convention as every other
batch tool in this project), in the order the files are given (current
table/selection order, matching RenameByPatternDialog/
ParseFilenameDialog's own established behavior rather than imposing a
separate sort).

Two modes depending on the chosen field's type (core.video_metadata's
NUMERIC_FIELDS vs TEXT_FIELDS):
- Numeric field (season_number, episode_number, personal_rating): the
  generated number IS the new value, written directly.
- Text field (Title, Track Title, etc.): the generated number is
  prefixed onto the field's EXISTING value with a separator --
  "Pilot" -> "01 - Pilot" -- not a full overwrite, since a text field
  usually already has meaningful content worth keeping.

Stages changes onto vf.metadata and marks vf.dirty = True, same "Apply
doesn't mean Save" principle as the other new batch tools. Actual
number-generation logic lives in core/text_transforms.py, kept
separate specifically so it's testable without PyQt6.

NOTE: not runnable in this sandbox -- no PyQt6. Syntax-checked and
reviewed only.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QComboBox, QLabel, QPushButton,
    QLineEdit, QSpinBox, QTableWidget, QTableWidgetItem,
)

from core.video_metadata import TEXT_FIELDS, NUMERIC_FIELDS
from core.text_transforms import generate_auto_number, apply_auto_number_to_text_field
from gui.tag_panel import FIELD_LABELS


class AutoNumberingDialog(QDialog):
    def __init__(self, video_files: list, parent=None):
        super().__init__(parent)
        self.video_files = video_files
        self.numbered_count = 0

        self.setWindowTitle(f"Auto-Numbering ({len(video_files)} file(s))")
        self.resize(720, 520)

        layout = QVBoxLayout(self)

        field_row = QHBoxLayout()
        field_row.addWidget(QLabel("Field:"))
        self.field_combo = QComboBox()
        # Numeric fields first -- auto-numbering into season/episode/
        # rating is the more common, more directly-numeric use case;
        # text-field prefixing is the secondary, more general one.
        for field_name in NUMERIC_FIELDS + TEXT_FIELDS:
            self.field_combo.addItem(FIELD_LABELS.get(field_name, field_name), field_name)
        self.field_combo.currentIndexChanged.connect(self._on_field_changed)
        field_row.addWidget(self.field_combo)
        field_row.addStretch()
        layout.addLayout(field_row)

        numbers_row = QHBoxLayout()
        numbers_row.addWidget(QLabel("Start:"))
        self.start_spin = QSpinBox()
        self.start_spin.setRange(-9999, 9999)
        self.start_spin.setValue(1)
        self.start_spin.valueChanged.connect(self._update_preview)
        numbers_row.addWidget(self.start_spin)

        numbers_row.addWidget(QLabel("Increment:"))
        self.increment_spin = QSpinBox()
        self.increment_spin.setRange(-999, 999)
        self.increment_spin.setValue(1)
        self.increment_spin.valueChanged.connect(self._update_preview)
        numbers_row.addWidget(self.increment_spin)

        numbers_row.addWidget(QLabel("Padding:"))
        self.padding_spin = QSpinBox()
        self.padding_spin.setRange(0, 6)
        self.padding_spin.setValue(2)
        self.padding_spin.valueChanged.connect(self._update_preview)
        numbers_row.addWidget(self.padding_spin)
        numbers_row.addStretch()
        layout.addLayout(numbers_row)

        separator_row = QHBoxLayout()
        self.separator_label = QLabel("Separator (text fields only):")
        separator_row.addWidget(self.separator_label)
        self.separator_edit = QLineEdit(" - ")
        self.separator_edit.textChanged.connect(self._update_preview)
        separator_row.addWidget(self.separator_edit)
        separator_row.addStretch()
        layout.addLayout(separator_row)

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

        self._on_field_changed()  # sets separator enabled state + first preview

    def _current_field(self) -> str:
        return self.field_combo.currentData()

    def _is_numeric_field(self) -> bool:
        return self._current_field() in NUMERIC_FIELDS

    def _on_field_changed(self) -> None:
        is_numeric = self._is_numeric_field()
        self.separator_edit.setEnabled(not is_numeric)
        self.separator_label.setEnabled(not is_numeric)
        self._update_preview()

    def _compute_new_value(self, vf, index: int) -> tuple:
        """Returns (current_value_display, new_value_display, new_value_to_store)."""
        field_name = self._current_field()
        start = self.start_spin.value()
        increment = self.increment_spin.value()
        padding = self.padding_spin.value()
        number_str = generate_auto_number(index, start, increment, padding)

        if self._is_numeric_field():
            current = getattr(vf.metadata, field_name, None)
            current_display = "" if current is None else str(current)
            new_value_to_store = int(number_str)  # padding is display-only for a real int field
            return current_display, str(new_value_to_store), new_value_to_store
        else:
            separator = self.separator_edit.text()
            current = getattr(vf.metadata, field_name, "") or ""
            new_value_to_store = apply_auto_number_to_text_field(current, number_str, separator)
            return current, new_value_to_store, new_value_to_store

    def _update_preview(self) -> None:
        self.preview_table.setRowCount(len(self.video_files))
        for row, vf in enumerate(self.video_files):
            current_display, new_display, _ = self._compute_new_value(vf, row)
            self.preview_table.setItem(row, 0, QTableWidgetItem(vf.path.name))
            self.preview_table.setItem(row, 1, QTableWidgetItem(current_display))
            self.preview_table.setItem(row, 2, QTableWidgetItem(new_display))

    def _on_apply(self) -> None:
        field_name = self._current_field()
        count = 0
        for row, vf in enumerate(self.video_files):
            _, _, new_value_to_store = self._compute_new_value(vf, row)
            setattr(vf.metadata, field_name, new_value_to_store)
            vf.dirty = True
            count += 1
        self.numbered_count = count
        self.accept()
