"""
gui/lyrics_panel.py

The LRCLIB lyrics page, used by two dialogs:

  * the "Lyrics (LRCLIB)" page of the subtitle search dialog (default mode): synced lyrics as subtitles for a music
    video; an entry without timing is greyed and cannot be chosen;
  * the Get Lyrics dialog (text_mode=True): the lyrics themselves, to be saved INSIDE the file. An entry is usable
    when it has any lyrics (plain, or synced with the timestamps stripped); instrumental entries say so and offer
    nothing. Two checkboxes: "Save the lyrics in the file" (default ON) and "Also save the timed lyrics as a
    subtitle file (.srt)" (default OFF, enabled only for entries with timing; the offset and language below apply
    only to the .srt).

  Artist / Title    what is searched (from the tags, else the file name "Artist - Title"); editable
  Results           title / artist / album, the track's length against the video's, a match label (exact, close,
                    different length with the difference) and what the entry holds
  Preview           the first 8 SRT cues (subtitle mode) or the lyrics text (text mode) of the selected entry
  Offset (seconds)  -30..+30 in 0.5 steps: updates the SRT preview live and is applied when the .srt is written
  Language          the lyrics' language for the .srt file name (LRCLIB has no language field)

The search runs on a worker thread under the shared progress dialog: it shows 'LRCLIB is busy (HTTP 503), trying
again in N s...' live while the client waits, Cancel works during the waits, and a failure leaves the real reason in
the status line with a "Try again" button (no modal box). The user picks explicitly; nothing is written here.
"""

from __future__ import annotations

import inspect
from typing import Callable, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QFontDatabase
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)
from redactor_common.core import lrclib
from redactor_common.gui import work_progress

from core import lyrics_subtitles

SYNC_WARNING_BG = QColor(255, 244, 200)
SYNC_WARNING_FG = QColor(70, 55, 0)
OFFSET_RANGE = (-30.0, 30.0)
PREVIEW_CUES = 8
SEARCHING_LABEL = "Looking up lyrics on LRCLIB..."
ATTRIBUTION = "Lyrics from LRCLIB (community-contributed)"
INSTRUMENTAL_NOTE = "This entry is marked instrumental: there are no lyrics to save."


def _clock(seconds: Optional[float]) -> str:
    if not seconds:
        return "?"
    total = int(round(seconds))
    return f"{total // 60}:{total % 60:02d}"


def _accepts_hooks(fn: Callable) -> bool:
    """True when `fn` takes the live-status / cancel keywords (the real find_lyrics does, a test's fake need not)."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return "on_status" in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


class LyricsPanel(QWidget):
    selection_changed = pyqtSignal(bool)  # True when a usable entry is selected

    def __init__(self, video, parent=None, search: Callable | None = None, text_mode: bool = False):
        super().__init__(parent)
        self.video = video
        self.text_mode = text_mode
        self._search = search or lyrics_subtitles.find_lyrics
        query = lyrics_subtitles.query_for_video(video)
        self.album = query.album
        self.duration = query.duration
        self.matches: list[lrclib.Match] = []
        self.notes: list[str] = []

        layout = QVBoxLayout(self)
        self.status_label = QLabel(
            "Searching LRCLIB for lyrics..." if text_mode else "Searching LRCLIB for synced lyrics...")
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
        self.retry_button = QPushButton("Try again")
        self.retry_button.setToolTip("Ask LRCLIB again with the same artist and title")
        self.retry_button.clicked.connect(self.run_search)
        self.retry_button.setVisible(False)
        row.addWidget(self.retry_button)
        layout.addLayout(row)

        self.results_list = QListWidget()
        self.results_list.setMinimumHeight(110)
        self.results_list.itemSelectionChanged.connect(self._on_selection)
        layout.addWidget(self.results_list, 2)

        self.note_label = QLabel(ATTRIBUTION if text_mode else "")
        self.note_label.setWordWrap(True)
        layout.addWidget(self.note_label)

        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        if not text_mode:
            self.preview.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.preview.setPlaceholderText(
            "Select an entry to preview its lyrics." if text_mode else "Select an entry to preview its subtitle.")
        self.preview.setMinimumHeight(110)
        layout.addWidget(self.preview, 2)

        self.save_lyrics_box = QCheckBox("Save the lyrics in the file")
        self.save_srt_box = QCheckBox("Also save the timed lyrics as a subtitle file (.srt)")
        if text_mode:
            self.save_lyrics_box.setChecked(True)
            self.save_srt_box.setChecked(False)
            self.save_srt_box.setEnabled(False)
            self.save_lyrics_box.toggled.connect(self._on_selection)
            self.save_srt_box.toggled.connect(self._on_srt_toggled)
            layout.addWidget(self.save_lyrics_box)
            layout.addWidget(self.save_srt_box)

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
        if text_mode:
            # Only the .srt uses them.
            self.offset_spin.setEnabled(False)
            self.language_combo.setEnabled(False)

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

    def _find(self, query: lyrics_subtitles.LyricsQuery) -> list:
        """The search on a worker thread under the progress dialog (Cancel works, also during the waits before a
        retry, whose live text replaces the dialog's label)."""
        notes: list[str] = []
        hooks = _accepts_hooks(self._search)

        def work(progress):
            if hooks:
                return self._search(query, on_status=progress.set_label, should_cancel=progress.should_cancel,
                                    notes=notes)
            return self._search(query)

        result, _cancelled = work_progress.run_work_with_progress(
            self, 0, SEARCHING_LABEL, work, cancellable=True, title="Looking Up Lyrics",
            cancelling_text="Cancelling...",
        )
        self.notes = notes
        return result

    def run_search(self) -> None:
        query = self.query()
        self.retry_button.setVisible(False)
        if not query.title:
            self.status_label.setText("Enter a title to search for.")
            return
        try:
            matches = self._find(query)
        except lrclib.LrclibCancelled:
            self.status_label.setText("Cancelled. Press Try again to ask LRCLIB again.")
            self.retry_button.setVisible(True)
            return
        except lrclib.LrclibError as exc:
            self.matches = []
            self.results_list.clear()
            self.status_label.setText(str(exc))
            self.retry_button.setVisible(True)
            self._on_selection()
            return
        self.matches = list(matches)
        self._populate()

    # -- the list -------------------------------------------------------------------

    def _holds(self, lyrics: lrclib.Lyrics) -> str:
        """What the entry holds, for its list line."""
        if lyrics.instrumental:
            return "instrumental"
        if not self.text_mode:
            return "synced" if lyrics.has_timing else "no timing"
        has_text = bool(lrclib.lyrics_text(lyrics))
        if not has_text:
            return "no lyrics"
        return "lyrics, synced" if lyrics.has_timing else "lyrics, no timing"

    def _is_usable(self, lyrics: lrclib.Lyrics) -> bool:
        return bool(lrclib.lyrics_text(lyrics)) if self.text_mode else lyrics.has_timing

    def _item_text(self, match: lrclib.Match) -> str:
        l = match.lyrics
        album = f" / {l.album_name}" if l.album_name else ""
        return (
            f"{l.track_name} - {l.artist_name}{album}\n"
            f"    track {_clock(l.duration)}, video {_clock(self.duration)}: {match.score.label}  |  {self._holds(l)}"
        )

    def _populate(self) -> None:
        self.results_list.clear()
        usable = 0
        for match in self.matches:
            item = QListWidgetItem(self._item_text(match))
            item.setData(Qt.ItemDataRole.UserRole, match)
            if not self._is_usable(match.lyrics):
                item.setFlags(Qt.ItemFlag.NoItemFlags)  # greyed, cannot be chosen
                item.setForeground(QBrush(QColor(140, 140, 140)))
                if self.text_mode:
                    item.setToolTip(INSTRUMENTAL_NOTE if match.lyrics.instrumental else "This entry has no lyrics.")
                else:
                    item.setToolTip("Plain lyrics only: no timing, so it cannot become subtitles.")
            else:
                usable += 1
            self.results_list.addItem(item)
        if not self.matches:
            self.status_label.setText("Nothing found on LRCLIB for that artist and title.")
        else:
            searched = f"{self.artist_edit.text().strip()} - {self.title_edit.text().strip()}".strip(" -")
            what = "with lyrics" if self.text_mode else "with timing"
            self.status_label.setText(
                f"Searched LRCLIB for \"{searched}\": {len(self.matches)} result(s), {usable} {what}."
            )
        self.status_label.setToolTip("\n".join(self.notes))
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

    def current_lyrics_text(self) -> str:
        """The cleaned plain text of the selected entry ("" when nothing usable is selected)."""
        match = self.selected_match
        return lrclib.lyrics_text(match.lyrics) if match is not None else ""

    def wants_lyrics(self) -> bool:
        return self.text_mode and self.save_lyrics_box.isChecked()

    def wants_srt(self) -> bool:
        return self.text_mode and self.save_srt_box.isEnabled() and self.save_srt_box.isChecked()

    def can_accept(self) -> bool:
        """True when the choice would write something: text mode needs the lyrics box (and lyrics) or the .srt
        box (and timing); subtitle mode needs a timed entry."""
        match = self.selected_match
        if match is None:
            return False
        if not self.text_mode:
            return match.lyrics.has_timing
        return (self.wants_lyrics() and bool(self.current_lyrics_text())) or (
            self.wants_srt() and bool(self.current_srt()))

    def _on_srt_toggled(self, _checked: bool = False) -> None:
        self._sync_srt_controls()
        self._on_selection()

    def _sync_srt_controls(self) -> None:
        if not self.text_mode:
            return
        match = self.selected_match
        timed = match is not None and match.lyrics.has_timing
        self.save_srt_box.setEnabled(timed)
        if not timed:
            self.save_srt_box.setChecked(False)
        srt_on = self.save_srt_box.isEnabled() and self.save_srt_box.isChecked()
        self.offset_spin.setEnabled(srt_on)
        self.language_combo.setEnabled(srt_on)

    def _on_selection(self, *_args) -> None:
        match = self.selected_match
        self._sync_srt_controls()
        self._update_preview()
        if self.text_mode:
            if match is not None and match.lyrics.instrumental:
                self.note_label.setText(INSTRUMENTAL_NOTE)
            else:
                self.note_label.setText(ATTRIBUTION)
        self.selection_changed.emit(bool(match is not None and self._is_usable(match.lyrics)))

    def _update_preview(self) -> None:
        match = self.selected_match
        if match is None:
            self.preview.setPlainText("")
            self.warning_label.setVisible(False)
            return
        if self.text_mode:
            if self.wants_srt() and not self.wants_lyrics():
                self.preview.setPlainText(lrclib.srt_preview(self.current_srt(), PREVIEW_CUES))
            else:
                self.preview.setPlainText(self.current_lyrics_text())
            show_warning = self.wants_srt() and not match.score.is_exact
        else:
            self.preview.setPlainText(lrclib.srt_preview(self.current_srt(), PREVIEW_CUES))
            show_warning = not match.score.is_exact
        self.warning_label.setText(lyrics_subtitles.SYNC_WARNING + (
            f" ({match.score.label})" if not match.score.is_exact else ""))
        self.warning_label.setVisible(show_warning)
