"""
PlaceholderReferenceList: a reference panel listing every valid
%field% pattern placeholder (core.video_metadata.EDITABLE_FIELDS,
labeled via the same FIELD_LABELS gui/tag_panel.py already uses),
built specifically so a user building a rename/parse pattern doesn't
have to remember exact field names like %season_number% by heart.

Reused by both gui/rename_pattern_dialog.py and
gui/parse_filename_dialog.py -- both need the identical reference,
and keeping it in one shared widget means the list can't drift out of
sync between the two dialogs the way two independently-maintained
copies could.

Double-clicking an entry inserts that placeholder into a target
QLineEdit at the current cursor position -- "see all the codes" is the
core request, but "insert without having to type it correctly" is a
natural, low-cost extension of the same idea.

NOTE: not runnable in this sandbox -- no PyQt6. Syntax-checked and
reviewed only.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QListWidget, QListWidgetItem, QLineEdit
from PyQt6.QtCore import Qt

from core.video_metadata import EDITABLE_FIELDS
from gui.tag_panel import FIELD_LABELS


class PlaceholderReferenceList(QWidget):
    """Usage: PlaceholderReferenceList(target_line_edit=self.pattern_edit, parent=self)
    -- double-clicking an entry inserts "%field_name%" into
    target_line_edit at the cursor position. target_line_edit can also
    be set/changed later via set_target().
    """

    def __init__(self, target_line_edit: QLineEdit = None, parent=None):
        super().__init__(parent)
        self._target = target_line_edit

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("Available codes (double-click to insert):"))

        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self.list_widget)

        # EDITABLE_FIELDS order matches the bulk-edit panel's own field
        # ordering (content_type first, etc.) -- same list, same order,
        # rather than re-deriving a different one here that could look
        # inconsistent with the panel the user already knows.
        for field_name in EDITABLE_FIELDS:
            label = FIELD_LABELS.get(field_name, field_name)
            item = QListWidgetItem(f"%{field_name}%  —  {label}")
            item.setData(Qt.ItemDataRole.UserRole, field_name)
            self.list_widget.addItem(item)

    def set_target(self, line_edit: QLineEdit) -> None:
        self._target = line_edit

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        if self._target is None:
            return
        field_name = item.data(Qt.ItemDataRole.UserRole)
        placeholder = f"%{field_name}%"
        cursor_pos = self._target.cursorPosition()
        current_text = self._target.text()
        new_text = current_text[:cursor_pos] + placeholder + current_text[cursor_pos:]
        self._target.setText(new_text)
        self._target.setCursorPosition(cursor_pos + len(placeholder))
        self._target.setFocus()
