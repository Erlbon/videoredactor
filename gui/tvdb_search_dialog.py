"""
TVDBSearchDialog: candidate picker for TheTVDB metadata import.

TV-only (no movie mode) -- TheTVDB is being added specifically as an
alternative TV metadata source alongside TMDB, not as a movie source;
TMDB remains the source for movies unchanged.

Same "always shown, never auto-apply" principle as TMDBSearchDialog:
the user picks the specific right show every time, since a wrong pick
writes wrong metadata to the actual file.

NOTE: not runnable in this sandbox -- no PyQt6, no network. Syntax-
checked and reviewed only.
"""

from __future__ import annotations
from typing import Optional

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QLineEdit, QPushButton, QLabel, QTextEdit, QMessageBox,
)
from PyQt6.QtCore import Qt

from core.tvdb_client import search_series, SeriesCandidate, TVDBError


class TVDBSearchDialog(QDialog):
    """Search TheTVDB and let the user pick exactly one series.

    Usage: dialog = TVDBSearchDialog(initial_query=guess);
    if dialog.exec(): selected = dialog.selected_candidate
    """

    def __init__(self, initial_query: str = "", parent=None):
        super().__init__(parent)
        self.selected_candidate: Optional[SeriesCandidate] = None
        self._candidates: list[SeriesCandidate] = []

        self.setWindowTitle("Search TheTVDB (TV Show)")
        self.resize(500, 500)

        layout = QVBoxLayout(self)

        search_row = QHBoxLayout()
        self.query_edit = QLineEdit(initial_query)
        self.query_edit.returnPressed.connect(self._on_search)
        search_row.addWidget(self.query_edit)
        self.search_button = QPushButton("Search")
        self.search_button.clicked.connect(self._on_search)
        search_row.addWidget(self.search_button)
        layout.addLayout(search_row)

        self.results_list = QListWidget()
        self.results_list.itemSelectionChanged.connect(self._on_selection_changed)
        self.results_list.itemDoubleClicked.connect(self._on_accept)
        layout.addWidget(self.results_list)

        self.overview_label = QTextEdit()
        self.overview_label.setReadOnly(True)
        self.overview_label.setMaximumHeight(100)
        layout.addWidget(self.overview_label)

        button_row = QHBoxLayout()
        button_row.addStretch()
        self.select_button = QPushButton("Use Selected")
        self.select_button.setEnabled(False)
        self.select_button.clicked.connect(self._on_accept)
        button_row.addWidget(self.select_button)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        button_row.addWidget(self.cancel_button)
        layout.addLayout(button_row)

        if initial_query:
            self._on_search()

    def _on_search(self) -> None:
        query = self.query_edit.text().strip()
        if not query:
            return

        self.results_list.clear()
        self.overview_label.clear()
        self.select_button.setEnabled(False)

        try:
            self._candidates = search_series(query)
        except TVDBError as e:
            QMessageBox.warning(self, "TheTVDB Search Failed", str(e))
            self._candidates = []
            return

        if not self._candidates:
            self.results_list.addItem("No results found")
            return

        for candidate in self._candidates:
            year_part = f" ({candidate.year})" if candidate.year else ""
            label = f"{candidate.name}{year_part}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, candidate)
            self.results_list.addItem(item)

    def _on_selection_changed(self) -> None:
        items = self.results_list.selectedItems()
        if not items:
            self.select_button.setEnabled(False)
            self.overview_label.clear()
            return
        candidate = items[0].data(Qt.ItemDataRole.UserRole)
        if candidate is None:  # the "No results found" placeholder item
            self.select_button.setEnabled(False)
            return
        self.select_button.setEnabled(True)
        self.overview_label.setPlainText(candidate.overview or "(no synopsis available)")

    def _on_accept(self) -> None:
        items = self.results_list.selectedItems()
        if not items:
            return
        candidate = items[0].data(Qt.ItemDataRole.UserRole)
        if candidate is None:
            return
        self.selected_candidate = candidate
        self.accept()
