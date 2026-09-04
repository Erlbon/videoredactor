"""
VocabularyEditorDialog: generic Add/Remove list editor, reused for both
Genres and Languages (matches the epub tool's own "Add/Remove
Genres..."/"Add/Remove Languages..." settings pattern).

Parameterized by a (get, add, remove) function triple rather than
having two near-duplicate dialog classes -- genres and languages are
structurally identical concepts (a named, user-editable pick-list),
just backed by different core.controlled_vocab functions.

NOTE: not runnable in this sandbox -- no PyQt6. Syntax-checked and
reviewed only.
"""

from __future__ import annotations
from typing import Callable

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QLineEdit,
    QPushButton, QLabel, QMessageBox,
)


class VocabularyEditorDialog(QDialog):
    """Usage:
        dialog = VocabularyEditorDialog(
            "Genres", get_genre_options, add_genre_option, remove_genre_option, parent=self,
        )
        dialog.exec()
        if dialog.changed:  # True if anything was actually added/removed
            ...refresh whatever displays this list...
    """

    def __init__(
        self, title: str,
        get_options: Callable[[], list[str]],
        add_option: Callable[[str], None],
        remove_option: Callable[[str], None],
        parent=None,
    ):
        super().__init__(parent)
        self._get_options = get_options
        self._add_option = add_option
        self._remove_option = remove_option
        self.changed = False

        self.setWindowTitle(f"Add/Remove {title}")
        self.resize(400, 450)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            f"These {title.lower()} appear as options in the bulk-edit "
            f"panel's {title} picker. Removing one here only removes it "
            f"as a future option -- it won't be stripped from any file "
            f"that already has it set."
        ))

        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget)

        add_row = QHBoxLayout()
        self.new_item_edit = QLineEdit()
        self.new_item_edit.setPlaceholderText(f"New {title[:-1].lower()}...")  # "Genres" -> "genre"
        self.new_item_edit.returnPressed.connect(self._on_add)
        add_row.addWidget(self.new_item_edit)
        self.add_button = QPushButton("Add")
        self.add_button.clicked.connect(self._on_add)
        add_row.addWidget(self.add_button)
        layout.addLayout(add_row)

        remove_row = QHBoxLayout()
        remove_row.addStretch()
        self.remove_button = QPushButton("Remove Selected")
        self.remove_button.setEnabled(False)
        self.remove_button.clicked.connect(self._on_remove)
        remove_row.addWidget(self.remove_button)
        layout.addLayout(remove_row)

        button_row = QHBoxLayout()
        button_row.addStretch()
        self.done_button = QPushButton("Done")
        self.done_button.clicked.connect(self.accept)
        button_row.addWidget(self.done_button)
        layout.addLayout(button_row)

        self.list_widget.itemSelectionChanged.connect(
            lambda: self.remove_button.setEnabled(bool(self.list_widget.selectedItems()))
        )

        self._refresh_list()

    def _refresh_list(self) -> None:
        self.list_widget.clear()
        self.list_widget.addItems(self._get_options())

    def _on_add(self) -> None:
        name = self.new_item_edit.text().strip()
        if not name:
            return
        if name in self._get_options():
            QMessageBox.information(self, "Already Exists", f'"{name}" is already in the list.')
            return
        self._add_option(name)
        self.changed = True
        self.new_item_edit.clear()
        self._refresh_list()

    def _on_remove(self) -> None:
        items = self.list_widget.selectedItems()
        if not items:
            return
        name = items[0].text()
        self._remove_option(name)
        self.changed = True
        self._refresh_list()
