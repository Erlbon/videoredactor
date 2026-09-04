"""
TMDBSearchDialog: candidate picker for TMDB metadata import.

Per explicit instruction: ALWAYS shown, even for a single strong match --
no auto-apply of a "confident" top result. The user picks the specific
right movie/show every time, since a wrong pick here writes wrong
metadata to the actual file (much higher stakes than epub's ISBN lookup
picking a slightly-off cover).

NOTE: not runnable in this sandbox -- no PyQt6, no network. Syntax-checked
and reviewed only.
"""

from __future__ import annotations
from typing import Optional, Union

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QLineEdit, QPushButton, QLabel, QTextEdit, QMessageBox,
)
from PyQt6.QtCore import Qt

from core.tmdb_client import (
    search_movies, search_tv, MovieCandidate, TVCandidate, TMDBError,
)

Candidate = Union[MovieCandidate, TVCandidate]


class TMDBSearchDialog(QDialog):
    """Search TMDB and let the user pick exactly one candidate.

    Usage: dialog = TMDBSearchDialog(mode='movie', initial_query=guess);
    if dialog.exec(): selected = dialog.selected_candidate
    """

    def __init__(self, mode: str, initial_query: str = "", parent=None):
        super().__init__(parent)
        self.mode = mode  # 'movie' or 'tv'
        self.selected_candidate: Optional[Candidate] = None
        self._candidates: list[Candidate] = []

        self.setWindowTitle(f"Search TMDB ({'Movie' if mode == 'movie' else 'TV Show'})")
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
            if self.mode == "movie":
                self._candidates = search_movies(query)
            else:
                self._candidates = search_tv(query)
        except TMDBError as e:
            QMessageBox.warning(self, "TMDB Search Failed", str(e))
            self._candidates = []
            return

        if not self._candidates:
            self.results_list.addItem("No results found")
            return

        for candidate in self._candidates:
            label = self._candidate_label(candidate)
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, candidate)
            self.results_list.addItem(item)

    def _candidate_label(self, candidate: Candidate) -> str:
        if isinstance(candidate, MovieCandidate):
            year_part = f" ({candidate.year})" if candidate.year else ""
            return f"{candidate.title}{year_part}"
        year_part = f" ({candidate.year})" if candidate.year else ""
        return f"{candidate.name}{year_part}"

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
