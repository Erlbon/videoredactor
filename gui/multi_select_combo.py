"""
MultiSelectComboBox: a QComboBox-styled control showing a checkable list
when clicked, staying open across multiple checkbox clicks (a standard
Qt recipe: a checkable QStandardItemModel plus overriding the popup's
close behavior on item click).

Used for Genre and Language in the bulk-edit panel -- controlled
vocabulary (see core/controlled_vocab.py) rather than freeform typing,
since a file can genuinely have more than one genre or audio language.

NOTE: not runnable in this sandbox -- no PyQt6. Syntax-checked and
reviewed only, same as every other PyQt6-touching file in this project.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QComboBox, QStyledItemDelegate
from PyQt6.QtGui import QStandardItemModel, QStandardItem
from PyQt6.QtCore import Qt, pyqtSignal


class MultiSelectComboBox(QComboBox):
    """Displays checked items as comma-separated text in the closed
    combo box; clicking opens a checklist that stays open across
    multiple selections (closing on every click, the QComboBox default,
    would make picking 3 genres take 3 separate clicks-and-reopens).
    """

    selectionChanged = pyqtSignal()

    def __init__(self, options: list[str], parent=None):
        super().__init__(parent)
        self._options = options
        self._model = QStandardItemModel(self)
        for option in options:
            item = QStandardItem(option)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            item.setData(Qt.CheckState.Unchecked, Qt.ItemDataRole.CheckStateRole)
            self._model.appendRow(item)
        self.setModel(self._model)

        # Prevent the popup from closing on every item click -- the
        # standard workaround is intercepting the view's click handling
        # rather than relying on QComboBox's built-in close-on-select.
        self.view().pressed.connect(self._on_item_pressed)
        self.setEditable(True)
        self.lineEdit().setReadOnly(True)
        self.lineEdit().setText("")

    def _on_item_pressed(self, index) -> None:
        item = self._model.itemFromIndex(index)
        new_state = (
            Qt.CheckState.Unchecked
            if item.checkState() == Qt.CheckState.Checked
            else Qt.CheckState.Checked
        )
        item.setCheckState(new_state)
        self._refresh_display_text()
        self.selectionChanged.emit()

    def _refresh_display_text(self) -> None:
        selected = self.checked_items()
        self.lineEdit().setText(", ".join(selected))

    @property
    def options(self) -> list[str]:
        """The exact option list this widget was constructed with --
        exposed publicly so callers (e.g. TagPanel's serialize step)
        can use the widget's own snapshot as the canonical order rather
        than re-querying core.controlled_vocab, which could in
        principle have changed between this widget's construction and
        a later read (the option lists are now user-editable via
        Settings, not fixed constants).
        """
        return list(self._options)

    def checked_items(self) -> list[str]:
        """Return checked items in the widget's own canonical (options
        list) order -- matches core.controlled_vocab.serialize_multi_value's
        ordering convention, so display and storage never disagree.
        """
        result = []
        for row in range(self._model.rowCount()):
            item = self._model.item(row)
            if item.checkState() == Qt.CheckState.Checked:
                result.append(item.text())
        return result

    def set_checked_items(self, values: set[str]) -> None:
        """Set which items are checked from a set of selected values
        (e.g. from core.controlled_vocab.parse_multi_value). Values not
        present in this widget's options list are silently ignored here
        -- the widget can only check boxes for options it actually has;
        core.controlled_vocab.serialize_multi_value is what preserves
        an unrecognized value in the underlying stored string, not this
        widget's display.
        """
        for row in range(self._model.rowCount()):
            item = self._model.item(row)
            item.setCheckState(
                Qt.CheckState.Checked if item.text() in values else Qt.CheckState.Unchecked
            )
        self._refresh_display_text()

    def clear_selection(self) -> None:
        self.set_checked_items(set())
