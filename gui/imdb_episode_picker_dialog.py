"""
ImdbEpisodePickerDialog: pick a season + episode of a series from the LOCAL
IMDb database, the offline counterpart of TVEpisodePickerDialog (same
`selected_season` / `selected_episode` interface, pre-selection from the
filename, nothing applied until the user confirms). IMDb's datasets have no
plot, so the detail box shows the episode's year, runtime and rating only.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from core import imdb_local
from core.imdb_import import ImdbDatabaseError


class ImdbEpisodePickerDialog(QDialog):
    """Usage: dialog = ImdbEpisodePickerDialog(db_path, series_tconst, series_name, parent=self);
    if dialog.exec(): season, episode = dialog.selected_season, dialog.selected_episode"""

    def __init__(
        self, db_path: str, series: int, series_name: str = "", initial_season: Optional[int] = None,
        initial_episode: Optional[int] = None, parent=None,
    ):
        super().__init__(parent)
        self.db_path = db_path
        self.series = series
        self._initial_season = initial_season
        self._initial_episode = initial_episode
        self.selected_season: Optional[int] = None
        self.selected_episode: Optional[imdb_local.ImdbEpisodeInfo] = None

        self.setWindowTitle("Select Season & Episode (IMDb Local Database)")
        self.resize(450, 500)
        layout = QVBoxLayout(self)
        if series_name:
            layout.addWidget(QLabel(f"<b>{series_name}</b>"))
        layout.addWidget(QLabel("Season:"))
        self.season_combo = QComboBox()
        self.season_combo.currentIndexChanged.connect(self._on_season_changed)
        layout.addWidget(self.season_combo)
        layout.addWidget(QLabel("Episode:"))
        self.episode_list = QListWidget()
        self.episode_list.itemSelectionChanged.connect(self._on_episode_selection_changed)
        self.episode_list.itemDoubleClicked.connect(self._on_accept)
        layout.addWidget(self.episode_list)
        self.overview_box = QTextEdit()
        self.overview_box.setReadOnly(True)
        self.overview_box.setMaximumHeight(100)
        layout.addWidget(self.overview_box)

        button_row = QHBoxLayout()
        button_row.addStretch()
        self.select_button = QPushButton("Use Selected Episode")
        self.select_button.setEnabled(False)
        self.select_button.clicked.connect(self._on_accept)
        button_row.addWidget(self.select_button)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        button_row.addWidget(cancel)
        layout.addLayout(button_row)
        self._load_seasons()

    def _db(self):
        return imdb_local.open_database(self.db_path)

    def _load_seasons(self) -> None:
        try:
            seasons = imdb_local.seasons_of(self._db(), self.series)
        except ImdbDatabaseError as exc:
            QMessageBox.warning(self, "Could Not Load Seasons", str(exc))
            self.reject()
            return
        if not seasons:
            QMessageBox.information(
                self, "No Episodes Found",
                "The local IMDb database has no episodes for this show (it may have been built without episodes, "
                "or without the title.episode file).",
            )
            self.reject()
            return
        for season, count in seasons:
            self.season_combo.addItem(f"Specials ({count} episodes)" if season == 0 else f"Season {season} ({count} episodes)",
                                      season)
        if self._initial_season is not None:
            index = self.season_combo.findData(self._initial_season)
            if index >= 0:
                self.season_combo.setCurrentIndex(index)

    def _on_season_changed(self) -> None:
        season = self.season_combo.currentData()
        if season is None:
            return
        self.episode_list.clear()
        self.overview_box.clear()
        self.select_button.setEnabled(False)
        try:
            episodes = imdb_local.episodes_of(self._db(), self.series, season)
        except ImdbDatabaseError as exc:
            QMessageBox.warning(self, "Could Not Load Episodes", str(exc))
            return
        select_row = -1
        for row, ep in enumerate(episodes):
            item = QListWidgetItem(f"E{ep.episode_number}: {ep.name}" if ep.name else f"Episode {ep.episode_number}")
            item.setData(Qt.ItemDataRole.UserRole, ep)
            self.episode_list.addItem(item)
            if (self._initial_episode is not None and season == self._initial_season
                    and ep.episode_number == self._initial_episode):
                select_row = row
        if select_row >= 0:
            self.episode_list.setCurrentRow(select_row)

    def _on_episode_selection_changed(self) -> None:
        items = self.episode_list.selectedItems()
        self.select_button.setEnabled(bool(items))
        if not items:
            self.overview_box.clear()
            return
        self.overview_box.setPlainText(items[0].data(Qt.ItemDataRole.UserRole).overview or "")

    def _on_accept(self) -> None:
        items = self.episode_list.selectedItems()
        if not items:
            return
        self.selected_season = self.season_combo.currentData()
        self.selected_episode = items[0].data(Qt.ItemDataRole.UserRole)
        self.accept()
