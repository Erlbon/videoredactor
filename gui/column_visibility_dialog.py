"""
ColumnVisibilityDialog: standalone "Add/Remove Columns..." dialog,
matching the epub tool's Settings menu item of the same name.

This manages the SAME persisted hidden-columns state
(core/table_settings.py's is_column_visible()/sanitize_hidden_fields(),
settings.ini's [table] hidden_columns key) the file table's own
right-click column-header context menu already exposes -- this dialog
is a second, more discoverable entry point to that one shared state,
not a separate/parallel mechanism. Toggling a column here and toggling
it via the header right-click menu both read and write the exact same
setting.

That same setting ALSO now drives which fields the bulk-edit panel
shows (gui/tag_panel.py's set_content_type_filter() filters its field
list through the same is_column_visible() check) -- per explicit
request, hiding a field here is meant to reclaim panel screen space
for fields the user doesn't use, not just declutter the table. Both
main_window.py entry points that can hide a column (this dialog, and
the header's own right-click menu) trigger a panel refresh immediately
after, so the "also hides the panel field" effect isn't limited to
just this dialog.

Shows every column the app knows about (not just whatever's currently
visible under the active Content Type filter) -- hidden-columns state
is global, applying across every filter, matching how it already
behaves via the header menu.

NOTE: not runnable in this sandbox -- no PyQt6. Syntax-checked and
reviewed only.
"""

from __future__ import annotations
from typing import Callable

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QPushButton, QLabel,
)
from PyQt6.QtCore import Qt

from core.table_settings import is_column_visible, sanitize_hidden_fields, PROTECTED_COLUMNS


class ColumnVisibilityDialog(QDialog):
    """Usage:
        dialog = ColumnVisibilityDialog(
            all_columns=[(field_name, label), ...],
            get_hidden=self._load_hidden_fields,
            set_hidden=lambda hidden: set_setting("table", "hidden_columns", ",".join(sorted(hidden))),
            parent=self,
        )
        dialog.exec()
        if dialog.changed:
            ...trigger a table column rebuild...
    """

    def __init__(
        self,
        all_columns: list[tuple[str, str]],
        get_hidden: Callable[[], set[str]],
        set_hidden: Callable[[set[str]], None],
        parent=None,
    ):
        super().__init__(parent)
        self._all_columns = all_columns
        self._get_hidden = get_hidden
        self._set_hidden = set_hidden
        self.changed = False

        self.setWindowTitle("Add/Remove Columns")
        self.resize(350, 450)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Check the fields you want visible. Unchecking one hides "
            "it from BOTH the file table and the bulk-edit panel -- "
            "handy for reclaiming screen space on fields you don't use. "
            "Filename can't be hidden -- it's how rows are identified."
        ))

        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget)

        button_row = QHBoxLayout()
        button_row.addStretch()
        self.done_button = QPushButton("Done")
        self.done_button.clicked.connect(self.accept)
        button_row.addWidget(self.done_button)
        layout.addLayout(button_row)

        self._populate()

    def _populate(self) -> None:
        hidden = sanitize_hidden_fields(self._get_hidden())
        for field_name, label in self._all_columns:
            item = QListWidgetItem(label)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            if field_name in PROTECTED_COLUMNS:
                # Filename: always checked, not interactable -- same
                # "never hideable" rule the header context menu already
                # enforces by simply not offering it as an entry at all;
                # shown here (rather than omitted) so this dialog's list
                # matches "every column the app has" without a silent gap.
                item.setCheckState(Qt.CheckState.Checked)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            else:
                visible = is_column_visible(field_name, hidden)
                item.setCheckState(Qt.CheckState.Checked if visible else Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, field_name)
            self.list_widget.addItem(item)

        self.list_widget.itemChanged.connect(self._on_item_changed)

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        field_name = item.data(Qt.ItemDataRole.UserRole)
        if field_name in PROTECTED_COLUMNS:
            return
        hidden = self._get_hidden()
        if item.checkState() == Qt.CheckState.Checked:
            hidden.discard(field_name)
        else:
            hidden.add(field_name)
        self._set_hidden(sanitize_hidden_fields(hidden))
        self.changed = True
