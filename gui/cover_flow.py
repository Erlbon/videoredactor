"""
gui/cover_flow.py

Metadata > Look Up > Get Cover (iTunes, MusicBrainz, Discogs): a cover for the ONE selected music video.

  1. the Get Cover dialog: artist and title prefilled (the tags, else the file name "Artist - Title") and editable,
     Search runs core/music_cover.find_covers under a cancellable busy dialog, the candidates are shown side by side
     as pictures with their source, release and size, the file's current poster (if any) beside the selected one;
  2. "Use Cover" prepares the picked one as the file's poster (core/poster.prepare_poster, ALBUM_KIND: JPEG, at most
     1000 pixels wide and 400 KB) and stages it through the same review as Get Poster (a poster the file already has
     is replaced only when its row stays ticked). Save All embeds it; Undo takes it back.

Cancel anywhere changes nothing.
"""

from __future__ import annotations

from typing import Callable, Optional

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QIcon, QImage, QPixmap
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QListView, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout,
)
from redactor_common.gui.owned_dialog import OwnedDialog

from core import lyrics_subtitles, music_cover
from core.poster import ALBUM_KIND, PosterRejected, PreparedPoster, prepare_poster
from gui.lookup import CANCELLED, run_cancellable_lookup
from gui.lyrics_flow import lyrics_target
from gui.source_apply import SourceStage, apply_sources

TITLE = "Get Cover"
ONLY_MUSIC_VIDEO = "Get Cover works on one Music Video: select exactly one file whose Content Type is Music Video."
ICON_SIDE = 150
CURRENT_SIDE = 150


def _pixmap(data: bytes, side: int) -> Optional[QPixmap]:
    image = QImage()
    if not data or not image.loadFromData(data) or image.isNull():
        return None
    return QPixmap.fromImage(image).scaled(side, side, Qt.AspectRatioMode.KeepAspectRatio,
                                           Qt.TransformationMode.SmoothTransformation)


class CoverDialog(OwnedDialog):
    """Nothing is written here. After exec(): `chosen` (the CoverCandidate) and `poster` (its PreparedPoster)."""

    def __init__(self, video, parent=None, find: Optional[Callable] = None):
        super().__init__(parent)
        self.video = video
        self.chosen: Optional[music_cover.CoverCandidate] = None
        self.poster: Optional[PreparedPoster] = None
        self._find = find or music_cover.find_covers
        self._started = False
        self.setWindowTitle("Get Cover (iTunes, MusicBrainz, Discogs)")
        self.resize(820, 620)

        query = lyrics_subtitles.query_for_video(video)
        outer = QVBoxLayout(self)
        self.status_label = QLabel("Searching for covers...")
        self.status_label.setWordWrap(True)
        outer.addWidget(self.status_label)

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
        outer.addLayout(row)

        body = QHBoxLayout()
        self.results_list = QListWidget()
        self.results_list.setViewMode(QListView.ViewMode.IconMode)
        self.results_list.setIconSize(QSize(ICON_SIDE, ICON_SIDE))
        self.results_list.setGridSize(QSize(ICON_SIDE + 40, ICON_SIDE + 60))
        self.results_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.results_list.setMovement(QListView.Movement.Static)
        self.results_list.setWordWrap(True)
        self.results_list.itemSelectionChanged.connect(self._on_selection)
        self.results_list.itemDoubleClicked.connect(lambda _item: self._on_accept())
        body.addWidget(self.results_list, 1)

        side = QVBoxLayout()
        side.addWidget(QLabel("Current poster:"))
        self.current_label = QLabel()
        self.current_label.setFixedSize(CURRENT_SIDE, CURRENT_SIDE)
        self.current_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        current = _pixmap(self._current_cover(), CURRENT_SIDE)
        if current is not None:
            self.current_label.setPixmap(current)
        else:
            self.current_label.setText("None")
        side.addWidget(self.current_label)
        side.addStretch()
        body.addLayout(side)
        outer.addLayout(body, 1)

        self.notes_label = QLabel("")
        self.notes_label.setWordWrap(True)
        outer.addWidget(self.notes_label)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.use_button = QPushButton("Use Cover")
        self.use_button.setToolTip("Stage the cover as the file's poster (embedded with Save All; Undo takes it back)")
        self.use_button.setEnabled(False)
        self.use_button.clicked.connect(self._on_accept)
        buttons.addWidget(self.use_button)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_button)
        outer.addLayout(buttons)

    def _current_cover(self) -> bytes:
        md = self.video.metadata
        if md.cover_pending:
            return md.cover_pending
        if not md.cover_info:
            return b""
        try:
            return self.video.read_cover() or b""
        except Exception:  # noqa: BLE001 -- the comparison picture only; the search works without it
            return b""

    # -- searching ----------------------------------------------------------------------

    def start(self) -> None:
        if not self._started:
            self._started = True
            self.run_search()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt)
        super().showEvent(event)
        if not self._started:
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, self.start)

    def run_search(self) -> None:
        import threading

        artist, title = self.artist_edit.text().strip(), self.title_edit.text().strip()
        cancel = threading.Event()
        results = run_cancellable_lookup(
            self, self._find, artist, title, should_cancel=cancel.is_set, cancel_event=cancel,
            label=f"Looking for covers of {artist + ' - ' if artist else ''}{title}...")
        self.results_list.clear()
        if results is CANCELLED:
            self.status_label.setText("Search cancelled.")
            self.notes_label.setText("")
            self._on_selection()
            return
        for candidate in results.candidates:
            item = QListWidgetItem(candidate.label())
            pixmap = _pixmap(candidate.data, ICON_SIDE)
            if pixmap is not None:
                item.setIcon(QIcon(pixmap))
            item.setData(Qt.ItemDataRole.UserRole, candidate)
            item.setToolTip(candidate.url)
            self.results_list.addItem(item)
        count = len(results.candidates)
        self.status_label.setText(f"{count} cover(s) found. Pick one." if count else "No cover found. Correct the "
                                  "artist or title and search again.")
        self.notes_label.setText("\n".join(results.notes))
        if count:
            self.results_list.setCurrentRow(0)
        self._on_selection()

    # -- result -------------------------------------------------------------------------

    def selected(self) -> Optional[music_cover.CoverCandidate]:
        items = self.results_list.selectedItems()
        return items[0].data(Qt.ItemDataRole.UserRole) if items else None

    def _on_selection(self) -> None:
        self.use_button.setEnabled(self.selected() is not None)

    def _on_accept(self) -> None:
        candidate = self.selected()
        if candidate is None:
            return
        try:
            self.poster = prepare_poster(candidate.data, ALBUM_KIND)
        except PosterRejected as exc:
            QMessageBox.warning(self, TITLE, f"That picture can't be used as the cover: {exc}")
            return
        self.chosen = candidate
        self.accept()


def run_get_cover(window, dialog_class=CoverDialog) -> None:
    vf = lyrics_target(window)
    if vf is None:
        window.status_bar.showMessage(ONLY_MUSIC_VIDEO)
        return
    dialog = dialog_class(vf, parent=window)
    if not dialog.exec() or dialog.poster is None:
        window.status_bar.showMessage("Get Cover: nothing chosen, nothing changed")
        return
    window._push_undo(TITLE, [vf])
    changed = apply_sources(window, [vf], {0: SourceStage(poster=dialog.poster)})
    window._refresh_table_rows()
    if window._selected_video_files():
        window._on_selection_changed()
    if changed:
        window.status_bar.showMessage(
            f"Get Cover: cover from {dialog.chosen.source} ({dialog.chosen.release}, {dialog.poster.description()}) "
            "staged, not yet saved to disk")
    else:
        window.status_bar.showMessage("Get Cover: kept the poster the file already has")
