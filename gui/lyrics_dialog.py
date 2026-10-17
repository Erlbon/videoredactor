"""
gui/lyrics_dialog.py

Metadata > Look Up > Get Lyrics (LRCLIB): the lyrics of one music video, saved INSIDE the file.

The page is the LRCLIB lyrics panel in text mode (gui/lyrics_panel.py): artist and title prefilled from the tags or the
file name and editable, the results with their match label and the track-against-video length, a preview of the text,
and the two checkboxes "Save the lyrics in the file" (default on) and "Also save the timed lyrics as a subtitle file
(.srt)" (default off, only for entries with timing; the offset and language apply only to the .srt).

Nothing is written here. After exec() the dialog reports the choice:
    selected_match   the lrclib.Match picked
    lyrics_text      the cleaned plain text to stage in the file ("" when "Save the lyrics in the file" is off)
    srt_text         the finished SRT (offset applied) ("" when the .srt box is off)
    srt_language     the language code for the .srt's file name
The flow (gui/lyrics_flow.py) stages the lyrics through the overwrite review and writes the optional sidecar.
"""

from __future__ import annotations

from typing import Callable, Optional

from PyQt6.QtWidgets import QDialog, QHBoxLayout, QPushButton, QVBoxLayout

from gui.lookup_file_header import add_file_header, file_context
from gui.lyrics_panel import LyricsPanel


class LyricsDialog(QDialog):
    def __init__(self, video, parent=None, search: Optional[Callable] = None, context=None):
        super().__init__(parent)
        self.video = video
        self.selected_match = None
        self.lyrics_text = ""
        self.srt_text = ""
        self.srt_language = ""
        self._started = False

        self.setWindowTitle("Get Lyrics (LRCLIB)")
        self.resize(620, 720)

        outer = QVBoxLayout(self)
        add_file_header(self, outer, context if context is not None else file_context(video))
        self.panel = LyricsPanel(video, search=search, text_mode=True)
        self.panel.selection_changed.connect(self._update_button)
        self.panel.save_lyrics_box.toggled.connect(self._update_button)
        self.panel.save_srt_box.toggled.connect(self._update_button)
        self.panel.results_list.itemDoubleClicked.connect(lambda _item: self._on_accept())
        outer.addWidget(self.panel, 1)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.save_button = QPushButton("Use Lyrics")
        self.save_button.setToolTip("Put the lyrics into the file's tags (saved to disk with Save All)")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self._on_accept)
        buttons.addWidget(self.save_button)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_button)
        outer.addLayout(buttons)

    # -- starting -----------------------------------------------------------------------

    def start(self) -> None:
        """Runs the first search (once). Called when the dialog is first shown, or by hand."""
        if not self._started:
            self._started = True
            self.panel.run_search()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt)
        super().showEvent(event)
        if not self._started:
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, self.start)

    # -- result -------------------------------------------------------------------------

    def _update_button(self, *_args) -> None:
        self.save_button.setEnabled(self.panel.can_accept())

    def _on_accept(self) -> None:
        panel = self.panel
        if not panel.can_accept():
            return
        self.selected_match = panel.selected_match
        self.lyrics_text = panel.current_lyrics_text() if panel.wants_lyrics() else ""
        self.srt_text = panel.current_srt() if panel.wants_srt() else ""
        self.srt_language = panel.language()
        self.accept()
