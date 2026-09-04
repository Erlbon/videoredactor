"""
ParseFilenameDialog: extract metadata field values FROM existing
filenames using a %field% pattern -- the reverse of
RenameByPatternDialog. Direct analog of the epub tool's Parse
Filename->Metadata feature, sharing the same pattern history.

Values are STAGED onto each VideoFile's metadata (dirty=True) rather
than written to disk immediately -- matches every other bulk-metadata
operation in this app (TMDB import, manual panel edits), all of which
require an explicit Save. Parsing a filename wrong should be as
recoverable as a mistyped field, not something that's already
permanently written to the file.

NOTE: not runnable in this sandbox -- no PyQt6. Syntax-checked and
reviewed only.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton, QLabel,
    QTableWidget, QTableWidgetItem, QMenu, QMessageBox, QSplitter, QWidget,
)
from PyQt6.QtCore import Qt

from core.filename_pattern import (
    parse_filename, load_pattern_history, save_pattern_to_history,
)
from gui.placeholder_reference import PlaceholderReferenceList


class ParseFilenameDialog(QDialog):
    """Usage: dialog = ParseFilenameDialog(video_files, parent=self);
    if dialog.exec(): the matched files' metadata has already been
    staged (dirty=True) -- caller should refresh the table/panel and
    remind the user Save is still required, same as after any other
    bulk-apply operation.
    """

    def __init__(self, video_files: list, parent=None):
        super().__init__(parent)
        self.video_files = video_files
        self.matched_count = 0

        self.setWindowTitle(f"Import Metadata from Filename ({len(video_files)} file(s))")
        self.resize(1000, 520)

        outer_layout = QVBoxLayout(self)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        outer_layout.addWidget(splitter)

        main_container = QVBoxLayout()

        pattern_row = QHBoxLayout()
        pattern_row.addWidget(QLabel("Pattern:"))
        self.pattern_edit = QLineEdit()
        self.pattern_edit.setPlaceholderText("%show_title% - S%season_number%E%episode_number% - %title%")
        self.pattern_edit.textChanged.connect(self._update_preview)
        pattern_row.addWidget(self.pattern_edit)

        self.history_button = QPushButton("\u25bc")
        self.history_button.setFixedWidth(28)
        self.history_button.clicked.connect(self._show_pattern_history_menu)
        pattern_row.addWidget(self.history_button)
        main_container.addLayout(pattern_row)

        self.preview_table = QTableWidget()
        self.preview_table.setColumnCount(2)
        self.preview_table.setHorizontalHeaderLabels(["Filename", "Extracted Fields"])
        main_container.addWidget(self.preview_table)

        self.status_label = QLabel("")
        main_container.addWidget(self.status_label)

        main_widget = QWidget()
        main_widget.setLayout(main_container)
        splitter.addWidget(main_widget)

        # Reference list visible alongside the pattern field -- same
        # reasoning as RenameByPatternDialog's identical setup: the
        # goal is not having to remember or look up placeholder names
        # while typing the pattern, so it needs to be visible AT THE
        # SAME TIME as the field being typed into, not tucked away.
        self.reference_list = PlaceholderReferenceList(target_line_edit=self.pattern_edit, parent=self)
        self.reference_list.setMinimumWidth(220)
        splitter.addWidget(self.reference_list)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)

        button_row = QHBoxLayout()
        button_row.addStretch()
        self.apply_button = QPushButton("Apply to Matched Files")
        self.apply_button.setEnabled(False)
        self.apply_button.clicked.connect(self._on_apply)
        button_row.addWidget(self.apply_button)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        button_row.addWidget(self.cancel_button)
        outer_layout.addLayout(button_row)

        history = load_pattern_history()
        if history:
            self.pattern_edit.setText(history[0])

    def _show_pattern_history_menu(self) -> None:
        history = load_pattern_history()
        if not history:
            return
        menu = QMenu(self)
        for pattern in history:
            action = menu.addAction(pattern)
            action.triggered.connect(lambda checked, p=pattern: self.pattern_edit.setText(p))
        menu.exec(self.history_button.mapToGlobal(self.history_button.rect().bottomLeft()))

    def _update_preview(self) -> None:
        pattern = self.pattern_edit.text()
        self.preview_table.setRowCount(len(self.video_files))
        self._parsed_results: dict[int, dict] = {}  # row -> field dict, only for matches
        match_count = 0

        for row, vf in enumerate(self.video_files):
            self.preview_table.setItem(row, 0, QTableWidgetItem(vf.path.name))

            if not pattern:
                self.preview_table.setItem(row, 1, QTableWidgetItem(""))
                continue

            parsed = parse_filename(vf.path.stem, pattern)
            if parsed is None:
                item = QTableWidgetItem("(no match)")
                item.setToolTip("This filename doesn't match the pattern")
            else:
                match_count += 1
                self._parsed_results[row] = parsed
                preview_text = ", ".join(f"{k}={v}" for k, v in parsed.items())
                item = QTableWidgetItem(preview_text)

            self.preview_table.setItem(row, 1, item)

        if pattern:
            self.status_label.setText(f"{match_count} of {len(self.video_files)} file(s) match this pattern")
        else:
            self.status_label.setText("")
        self.apply_button.setEnabled(bool(pattern) and match_count > 0)

    def _on_apply(self) -> None:
        pattern = self.pattern_edit.text()
        if not pattern:
            return

        applied = 0
        for row, vf in enumerate(self.video_files):
            parsed = self._parsed_results.get(row)
            if not parsed:
                continue  # this file didn't match -- left completely untouched

            for field_name, value in parsed.items():
                if field_name in ("season_number", "episode_number", "personal_rating"):
                    # These are int fields in VideoMetadata -- an
                    # extracted value that isn't actually numeric (e.g.
                    # a mismatched pattern that happened to still match
                    # structurally) is skipped for this field rather
                    # than crashing the whole apply pass or silently
                    # writing a string into an int field.
                    if value.isdigit():
                        setattr(vf.metadata, field_name, int(value))
                else:
                    setattr(vf.metadata, field_name, value)
            vf.dirty = True
            applied += 1

        save_pattern_to_history(pattern)
        self.matched_count = applied

        QMessageBox.information(
            self, "Parse Complete",
            f"Applied extracted metadata to {applied} file(s) -- not yet saved to disk. "
            f"Files that didn't match the pattern were left untouched."
        )
        self.accept()
