"""
gui/lyrics_panel.py

The "Lyrics (LRCLIB)" page of the subtitle search dialog: synced lyrics as subtitles for a music video.

  Artist / Title    what is searched (from the tags, else the file name "Artist - Title"); editable
  Results           title / artist / album, the track's length against the video's, a match label (exact, close,
                    different length with the difference) and "synced" or "no timing"; an entry without timing is
                    greyed and cannot be chosen
  Preview           the first 8 SRT cues of the selected entry, with the offset applied
  Offset (seconds)  -30..+30 in 0.5 steps: updates the preview live and is applied when the file is written
  Language          the lyrics' language for the file name (LRCLIB has no language field): prefilled from the video

The user picks explicitly; nothing is written here (the dialog reports the choice, the main window writes the
sidecar through VideoFile.save_subtitle_sidecar).
"""

from __future__ import annotations

from typing import Callable, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFontDatabase
from PyQt6.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)
from redactor_common.core import lrclib

from core import lyrics_subtitles
from gui.lookup import run_lookup

SYNC_WARNING_BG = QColor(255, 244, 200)
SYNC_WARNING_FG = QColor(70, 55, 0)
OFFSET_RANGE = (-30.0, 30.0)
PREVIEW_CUES = 8


def _clock(seconds: Optional[float]) -> str:
    if not seconds:
        return "?"
    total = int(round(seconds))
    return f"{total // 60}:{total % 60:02d}"


class LyricsPanel(QWidget):
    selection_changed = pyqtSignal(bool)  # True when a usable entry is selected

    def __init__(self, video, parent=None, search: Callable | None = None):
        super().__init__(parent)
        self.video = video
        self._search = search or lyrics_subtitles.find_lyrics
        query = lyrics_subtitles.query_for_video(video)
        self.album = query.album
        self.duration = query.duration
        self.matches: list[lrclib.Match] = []

        layout = QVBoxLayout(self)
        self.status_label = QLabel("Searching LRCLIB for synced lyrics...")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        row = QHBoxLayout()
        self.artist_edit = QLineEdit(query.artist)
        self.artist_edit.setPlaceholderText("Artist")
        self.title_edit = QLineEdit(query.title)
        self.title_edit.setPlaceholderText("Title")
        for edit in (self.artist_edit, self.title_edit):
            edit.returnPressed.connect(self.run_search)
            row.addWidget(edit)
        self.search_button = QPushButton("Search")
        self.search_button.clicked.connect(self.run_search)
        row.addWidget(self.search_button)
        layout.addLayout(row)

        self.results_list = QListWidget()
        self.results_list.setMinimumHeight(110)
        self.results_list.itemSelectionChanged.connect(self._on_selection)
        layout.addWidget(self.results_list, 2)

        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.preview.setPlaceholderText("Select an entry to preview its subtitle.")
        self.preview.setMinimumHeight(110)
        layout.addWidget(self.preview, 2)

        form = QFormLayout()
        self.offset_spin = QDoubleSpinBox()
        self.offset_spin.setRange(*OFFSET_RANGE)
        self.offset_spin.setSingleStep(0.5)
        self.offset_spin.setDecimals(1)
        self.offset_spin.setSuffix(" s")
        self.offset_spin.valueChanged.connect(self._update_preview)
        form.addRow("Offset (seconds):", self.offset_spin)
        self.language_combo = QComboBox()
        self.language_combo.setEditable(True)
        for code, name in lyrics_subtitles.language_choices():
            self.language_combo.addItem(f"{name} [{code}]" if code != "und" else name, code)
        self._select_language(lyrics_subtitles.guess_language(video))
        form.addRow("Lyrics language:", self.language_combo)
        layout.addLayout(form)

        self.warning_label = QLabel("")
        self.warning_label.setWordWrap(True)
        self.warning_label.setAutoFillBackground(True)
        self.warning_label.setStyleSheet(
            f"background-color: rgb({SYNC_WARNING_BG.red()},{SYNC_WARNING_BG.green()},{SYNC_WARNING_BG.blue()});"
            f"color: rgb({SYNC_WARNING_FG.red()},{SYNC_WARNING_FG.green()},{SYNC_WARNING_FG.blue()});padding: 4px;"
        )
        self.warning_label.setVisible(False)
        layout.addWidget(self.warning_label)

    # -- language ----------------------------------------------------------------

    def _select_language(self, code: str) -> None:
        index = self.language_combo.findData(code)
        if index >= 0:
            self.language_combo.setCurrentIndex(index)
        else:
            self.language_combo.setEditText(code)

    def language(self) -> str:
        """The code for the file name: the chosen entry's code, or what was typed (cleaned like every other
        subtitle language: anything odd becomes "und")."""
        from core.opensubtitles_client import clean_language_code
        text = self.language_combo.currentText().strip()
        index = self.language_combo.findText(text)
        if index >= 0:
            return clean_language_code(self.language_combo.itemData(index))
        if "[" in text and text.endswith("]"):
            text = text[text.rindex("[") + 1:-1]
        return clean_language_code(text.lower())

    # -- searching -------------------------------------------------------------------

    def query(self) -> lyrics_subtitles.LyricsQuery:
        return lyrics_subtitles.LyricsQuery(
            self.artist_edit.text().strip(), self.title_edit.text().strip(), self.album, self.duration
        )

    def run_search(self) -> None:
        query = self.query()
        if not query.title:
            self.status_label.setText("Enter a title to search for.")
            return
        try:
            matches = run_lookup(self, self._search, query)
        except lrclib.LrclibError as exc:
            QMessageBox.warning(self, "Lyrics Search Failed", str(exc))
            self.status_label.setText("The LRCLIB search failed: try again in a moment.")
            return
        self.matches = list(matches)
        self._populate()

    def _item_text(self, match: lrclib.Match) -> str:
        l = match.lyrics
        timing = "synced" if l.has_timing else "no timing"
        if l.instrumental:
            timing = "instrumental"
        album = f" / {l.album_name}" if l.album_name else ""
        return (
            f"{l.track_name} - {l.artist_name}{album}\n"
            f"    track {_clock(l.duration)}, video {_clock(self.duration)}: {match.score.label}  |  {timing}"
        )

    def _populate(self) -> None:
        self.results_list.clear()
        usable = 0
        for match in self.matches:
            item = QListWidgetItem(self._item_text(match))
            item.setData(Qt.ItemDataRole.UserRole, match)
            if not match.lyrics.has_timing:
                item.setFlags(Qt.ItemFlag.NoItemFlags)  # greyed, cannot be chosen
                item.setForeground(QBrush(QColor(140, 140, 140)))
                item.setToolTip("Plain lyrics only: no timing, so it cannot become subtitles.")
            else:
                usable += 1
            self.results_list.addItem(item)
        if not self.matches:
            self.status_label.setText("Nothing found on LRCLIB for that artist and title.")
        else:
            searched = f"{self.artist_edit.text().strip()} - {self.title_edit.text().strip()}".strip(" -")
            self.status_label.setText(
                f"Searched LRCLIB for \"{searched}\": {len(self.matches)} result(s), {usable} with timing."
            )
        self._on_selection()

    # -- selection / preview -----------------------------------------------------------

    @property
    def selected_match(self) -> Optional[lrclib.Match]:
        items = self.results_list.selectedItems()
        return items[0].data(Qt.ItemDataRole.UserRole) if items else None

    def offset(self) -> float:
        return float(self.offset_spin.value())

    def current_srt(self) -> str:
        match = self.selected_match
        if match is None or not match.lyrics.has_timing:
            return ""
        return lyrics_subtitles.srt_for(match.lyrics, self.duration, self.offset())

    def _on_selection(self) -> None:
        match = self.selected_match
        self._update_preview()
        self.selection_changed.emit(bool(match is not None and match.lyrics.has_timing))

    def _update_preview(self) -> None:
        match = self.selected_match
        if match is None:
            self.preview.setPlainText("")
            self.warning_label.setVisible(False)
            return
        self.preview.setPlainText(lrclib.srt_preview(self.current_srt(), PREVIEW_CUES))
        self.warning_label.setText(lyrics_subtitles.SYNC_WARNING + (
            f" ({match.score.label})" if not match.score.is_exact else ""))
        self.warning_label.setVisible(not match.score.is_exact)
