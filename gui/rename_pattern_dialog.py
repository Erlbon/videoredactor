"""
RenameByPatternDialog: batch rename/export files using a %field%
pattern, with a live preview table showing old name -> new name for
every selected file before anything touches disk.

Direct analog of the epub tool's Rename/Export by Pattern feature.
Pattern history is shared with ParseFilenameDialog (both read/write the
same settings.ini key) -- same "shared pattern history" the epub tool
uses between its own Rename and Parse Filename dialogs.

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
    render_filename, validate_filename_stem,
    load_pattern_history, save_pattern_to_history,
)
from gui.placeholder_reference import PlaceholderReferenceList


class RenameByPatternDialog(QDialog):
    """Usage: dialog = RenameByPatternDialog(video_files, parent=self);
    if dialog.exec(): the files have already been renamed on disk (this
    dialog performs the rename itself on Apply, same "acts immediately,
    not staged" precedent as the epub tool's Rename/Export tool).
    """

    def __init__(self, video_files: list, parent=None):
        super().__init__(parent)
        self.video_files = video_files
        self.renamed_count = 0

        self.setWindowTitle(f"Rename {len(video_files)} File(s) by Pattern")
        self.resize(950, 520)

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

        self.history_button = QPushButton("\u25bc")  # matches the epub tool's recent-patterns button glyph
        self.history_button.setFixedWidth(28)
        self.history_button.clicked.connect(self._show_pattern_history_menu)
        pattern_row.addWidget(self.history_button)
        main_container.addLayout(pattern_row)

        self.preview_table = QTableWidget()
        self.preview_table.setColumnCount(2)
        self.preview_table.setHorizontalHeaderLabels(["Current Name", "New Name"])
        main_container.addWidget(self.preview_table)

        self.warning_label = QLabel("")
        self.warning_label.setStyleSheet("color: #b02a2a;")
        main_container.addWidget(self.warning_label)

        main_widget = QWidget()
        main_widget.setLayout(main_container)
        splitter.addWidget(main_widget)

        # Reference list visible alongside the pattern field, not
        # tucked away somewhere requiring a scroll or a separate
        # window -- the whole point is not having to remember or look
        # up placeholder names while typing the pattern.
        self.reference_list = PlaceholderReferenceList(target_line_edit=self.pattern_edit, parent=self)
        self.reference_list.setMinimumWidth(220)
        splitter.addWidget(self.reference_list)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)

        button_row = QHBoxLayout()
        button_row.addStretch()
        self.apply_button = QPushButton("Rename Files")
        self.apply_button.clicked.connect(self._on_apply)
        button_row.addWidget(self.apply_button)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        button_row.addWidget(self.cancel_button)
        outer_layout.addLayout(button_row)

        last_pattern = load_pattern_history()
        if last_pattern:
            self.pattern_edit.setText(last_pattern[0])

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
        collision_check: dict[str, int] = {}
        problems = []

        for row, vf in enumerate(self.video_files):
            self.preview_table.setItem(row, 0, QTableWidgetItem(vf.path.name))
            if not pattern:
                self.preview_table.setItem(row, 1, QTableWidgetItem(""))
                continue

            new_stem = render_filename(vf.metadata, pattern)
            new_name = f"{new_stem}{vf.path.suffix}"
            item = QTableWidgetItem(new_name)

            error = validate_filename_stem(new_stem)
            if error:
                item.setToolTip(error)
                problems.append(f"{vf.path.name}: {error}")
            collision_check[new_name] = collision_check.get(new_name, 0) + 1

            self.preview_table.setItem(row, 1, item)

        collisions = [name for name, count in collision_check.items() if count > 1]
        if collisions:
            problems.append(f"{len(collisions)} filename collision(s) among the results")

        self.warning_label.setText("; ".join(problems) if problems else "")
        self.apply_button.setEnabled(bool(pattern) and not problems)

    def _on_apply(self) -> None:
        pattern = self.pattern_edit.text()
        if not pattern:
            return

        # Re-validate at apply time, not just relying on the preview's
        # last computed state -- metadata could theoretically have
        # changed between preview and click in a way that's stale.
        planned: list[tuple] = []
        seen_names = set()
        for vf in self.video_files:
            new_stem = render_filename(vf.metadata, pattern)
            error = validate_filename_stem(new_stem)
            if error:
                QMessageBox.warning(self, "Cannot Rename", f"{vf.path.name}: {error}")
                return
            new_path = vf.path.with_name(f"{new_stem}{vf.path.suffix}")
            if new_path in seen_names:
                QMessageBox.warning(self, "Cannot Rename", f"Collision: multiple files would become {new_path.name}")
                return
            seen_names.add(new_path)
            planned.append((vf, new_path))

        failed = []
        for vf, new_path in planned:
            if new_path.exists() and new_path != vf.path:
                failed.append((vf, f"{new_path.name} already exists"))
                continue
            try:
                vf.path.rename(new_path)
                vf.path = new_path
                self.renamed_count += 1
            except OSError as e:
                failed.append((vf, str(e)))

        save_pattern_to_history(pattern)

        if failed:
            details = "\n".join(f"{vf.path.name}: {err}" for vf, err in failed)
            QMessageBox.warning(self, "Some files failed to rename", details)

        self.accept()
