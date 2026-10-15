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
    QComboBox, QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QLineEdit, QPushButton, QLabel, QMessageBox, QStackedWidget, QWidget,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

from gui.lookup import run_lookup
from gui.lyrics_panel import LyricsPanel
from core.video_metadata import ContentType
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


SOURCE_OPENSUBTITLES = "opensubtitles"
SOURCE_LRCLIB = "lrclib"


class SubtitleSearchDialog(QDialog):
    """Usage: dialog = SubtitleSearchDialog(video_path, language='en', parent=self, video=vf);
    if dialog.exec(): dialog.source says which page was used:
      "opensubtitles": dialog.selected_candidate (download it);
      "lrclib": dialog.selected_srt (the finished SRT text, offset applied), dialog.selected_language,
                dialog.selected_match (an lrclib.Match).
    `video` (a VideoFile) enables the Lyrics (LRCLIB) source; it is the default for a Music Video."""

    def __init__(self, video_path: str, language: str = "en", parent=None, video=None, lyrics_search=None,
                 source: Optional[str] = None):
        super().__init__(parent)
        self.video_path = video_path
        self.language = language
        self.video = video
        self.selected_candidate: Optional[SubtitleCandidate] = None
        self.source = SOURCE_OPENSUBTITLES
        self.selected_srt = ""
        self.selected_language = ""
        self.selected_match = None
        self.translate_requested = False
        self.lyrics_panel: Optional[LyricsPanel] = None
        self._opensubtitles_started = False

        self.setWindowTitle("Import Subtitles")
        self.resize(560, 560)

        outer = QVBoxLayout(self)
        source_row = QHBoxLayout()
        source_row.addWidget(QLabel("Source:"))
        self.source_combo = QComboBox()
        self.source_combo.addItem("OpenSubtitles", SOURCE_OPENSUBTITLES)
        if video is not None:
            self.source_combo.addItem("Lyrics (LRCLIB)", SOURCE_LRCLIB)
        source_row.addWidget(self.source_combo, 1)
        outer.addLayout(source_row)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)

        page = QWidget()
        self.stack.addWidget(page)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

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

        if video is not None:
            self.lyrics_panel = LyricsPanel(video, search=lyrics_search)
            self.lyrics_panel.selection_changed.connect(self._update_select_button)
            self.stack.addWidget(self.lyrics_panel)

        button_row = QHBoxLayout()
        button_row.addStretch()
        self.select_button = QPushButton("Use Selected")
        self.select_button.setEnabled(False)
        self.select_button.clicked.connect(self._on_accept)
        button_row.addWidget(self.select_button)
        self.translate_button = QPushButton("Use and Translate...")
        self.translate_button.setToolTip(
            "Write the lyrics as a subtitle, then open the side-by-side English translation for it."
        )
        self.translate_button.clicked.connect(self._on_use_and_translate)
        button_row.addWidget(self.translate_button)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        button_row.addWidget(self.cancel_button)
        outer.addLayout(button_row)

        wanted = source
        if wanted is None:
            is_music = video is not None and video.metadata.content_type is ContentType.MUSIC_VIDEO
            wanted = SOURCE_LRCLIB if is_music else SOURCE_OPENSUBTITLES
        self.source_combo.currentIndexChanged.connect(self._on_source_changed)
        self.set_source(wanted)

    def set_source(self, source: str) -> None:
        """Show the OpenSubtitles or the Lyrics (LRCLIB) page; the first visit of a page starts its search."""
        if source == SOURCE_LRCLIB and self.lyrics_panel is None:
            source = SOURCE_OPENSUBTITLES
        index = self.source_combo.findData(source)
        self.source_combo.blockSignals(True)
        self.source_combo.setCurrentIndex(index)
        self.source_combo.blockSignals(False)
        self._on_source_changed()

    def _on_source_changed(self) -> None:
        self.source = self.source_combo.currentData()
        if self.source == SOURCE_LRCLIB:
            self.stack.setCurrentWidget(self.lyrics_panel)
            if not self.lyrics_panel.matches and not getattr(self.lyrics_panel, "_started", False):
                self.lyrics_panel._started = True
                self.lyrics_panel.run_search()
        else:
            self.stack.setCurrentIndex(0)
            if not self._opensubtitles_started:
                self._opensubtitles_started = True
                self._run_hash_search()
        self._update_select_button()

    def _update_select_button(self, *_args) -> None:
        if self.source == SOURCE_LRCLIB:
            match = self.lyrics_panel.selected_match
            self.select_button.setEnabled(bool(match is not None and match.lyrics.has_timing))
        else:
            self.select_button.setEnabled(bool(self.results_list.selectedItems()))
        self.translate_button.setVisible(self.source == SOURCE_LRCLIB)
        self.translate_button.setEnabled(self.source == SOURCE_LRCLIB and self.select_button.isEnabled())

    def _run_hash_search(self) -> None:
        try:
            results = run_lookup(self, search_by_hash, self.video_path, self.language)
        except (OpenSubtitlesError, OSError) as e:  # OSError: the file couldn't be read for hashing
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
            results = run_lookup(self, search_by_title, query, self.language)
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
        self._update_select_button()

    def _on_use_and_translate(self) -> None:
        """Lyrics page: write the chosen lyrics as usual, then the main window opens the translation for it."""
        self.translate_requested = True
        self._on_accept()
        if not self.result():
            self.translate_requested = False

    def _on_accept(self) -> None:
        if self.source == SOURCE_LRCLIB:
            match = self.lyrics_panel.selected_match
            srt = self.lyrics_panel.current_srt()
            if match is None or not srt:
                return
            self.selected_match = match
            self.selected_srt = srt
            self.selected_language = self.lyrics_panel.language()
            self.accept()
            return
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
