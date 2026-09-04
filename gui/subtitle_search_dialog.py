"""
SubtitleSearchDialog: hash-match-first subtitle picker.

On open, automatically runs a hash-based search against the selected
file (guaranteed-sync, per explicit instruction to always try this
first). If that returns nothing, offers a title-based fallback search --
every fallback result is shown with an explicit "sync not guaranteed"
warning, never presented with the same confidence as a hash match.

The user always picks explicitly from the results list -- no
auto-apply, same principle as TMDBSearchDialog and for the same reason:
a wrong subtitle written to disk (even just a sidecar file) is a worse
failure mode than a blank field, especially since sync problems aren't
obvious until someone's already watching.

NOTE: not runnable in this sandbox -- no PyQt6, no network. Syntax-
checked and reviewed only.
"""

from __future__ import annotations
from typing import Optional

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QLineEdit, QPushButton, QLabel, QMessageBox,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

from core.opensubtitles_client import (
    search_by_hash, search_by_title, SubtitleCandidate, OpenSubtitlesError,
)

# Matched (background, foreground) pair -- same DIRTY_BG/DIRTY_FG tint
# as the main table's "pay attention" color, and same reasoning: never
# pair a fixed background with the theme's default (unpredictable)
# text color. A dark theme's light default text on this light pastel
# background would be unreadable, the same bug class the main table's
# DIRTY_BG/DIRTY_FG fix addressed -- fixed here identically rather than
# left as a second instance of the same mistake.
SYNC_WARNING_BG = QColor(255, 244, 200)
SYNC_WARNING_FG = QColor(70, 55, 0)


class SubtitleSearchDialog(QDialog):
    """Usage: dialog = SubtitleSearchDialog(video_path, language='en', parent=self);
    if dialog.exec(): candidate = dialog.selected_candidate
    """

    def __init__(self, video_path: str, language: str = "en", parent=None):
        super().__init__(parent)
        self.video_path = video_path
        self.language = language
        self.selected_candidate: Optional[SubtitleCandidate] = None

        self.setWindowTitle("Import Subtitles from OpenSubtitles")
        self.resize(500, 450)

        layout = QVBoxLayout(self)

        self.status_label = QLabel("Checking for an exact (hash-matched) subtitle...")
        layout.addWidget(self.status_label)

        self.results_list = QListWidget()
        self.results_list.itemSelectionChanged.connect(self._on_selection_changed)
        self.results_list.itemDoubleClicked.connect(self._on_accept)
        layout.addWidget(self.results_list)

        fallback_row = QHBoxLayout()
        self.fallback_query_edit = QLineEdit()
        self.fallback_query_edit.setPlaceholderText("Search by title instead...")
        self.fallback_query_edit.returnPressed.connect(self._on_fallback_search)
        fallback_row.addWidget(self.fallback_query_edit)
        self.fallback_search_button = QPushButton("Search by Title")
        self.fallback_search_button.clicked.connect(self._on_fallback_search)
        fallback_row.addWidget(self.fallback_search_button)
        layout.addLayout(fallback_row)

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

        self._run_hash_search()

    def _run_hash_search(self) -> None:
        try:
            results = search_by_hash(self.video_path, self.language)
        except OpenSubtitlesError as e:
            QMessageBox.warning(self, "Subtitle Search Failed", str(e))
            self.status_label.setText(
                "Hash search failed -- you can still try a title search below."
            )
            return

        if results:
            self.status_label.setText(
                f"Found {len(results)} exact (hash-matched) subtitle(s) -- sync verified."
            )
            self._populate_results(results)
        else:
            self.status_label.setText(
                "No exact match found. Try a title search below -- "
                "results won't have guaranteed sync."
            )

    def _on_fallback_search(self) -> None:
        query = self.fallback_query_edit.text().strip()
        if not query:
            return
        try:
            results = search_by_title(query, self.language)
        except OpenSubtitlesError as e:
            QMessageBox.warning(self, "Subtitle Search Failed", str(e))
            return

        self.status_label.setText(
            f"Found {len(results)} title-search result(s) -- SYNC NOT GUARANTEED."
            if results else "No results found for that title."
        )
        self._populate_results(results)

    def _populate_results(self, results: list[SubtitleCandidate]) -> None:
        self.results_list.clear()
        for candidate in results:
            label = self._candidate_label(candidate)
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, candidate)
            if not candidate.hash_matched:
                # Matched background+foreground pair, not background
                # alone -- see SYNC_WARNING_BG/FG's comment for why.
                item.setBackground(SYNC_WARNING_BG)
                item.setForeground(SYNC_WARNING_FG)
            self.results_list.addItem(item)

    def _candidate_label(self, candidate: SubtitleCandidate) -> str:
        sync_note = "[EXACT MATCH]" if candidate.hash_matched else "[sync not guaranteed]"
        release = candidate.release_name or "(unnamed release)"
        return f"{sync_note} {release} -- {candidate.download_count} downloads"

    def _on_selection_changed(self) -> None:
        items = self.results_list.selectedItems()
        self.select_button.setEnabled(bool(items))

    def _on_accept(self) -> None:
        items = self.results_list.selectedItems()
        if not items:
            return
        candidate = items[0].data(Qt.ItemDataRole.UserRole)
        if candidate is None:
            return
        # Extra confirmation specifically for a non-hash-matched pick --
        # this is the one moment worth an explicit "are you sure," since
        # sync problems aren't discoverable until someone's mid-episode.
        if not candidate.hash_matched:
            reply = QMessageBox.question(
                self, "Sync Not Guaranteed",
                "This subtitle was found by title search, not an exact file match. "
                "It may be out of sync with this specific video. Use it anyway?",
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self.selected_candidate = candidate
        self.accept()
