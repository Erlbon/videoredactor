"""
TVEpisodePickerDialog: pick a specific season + episode for a matched
TV show, so episode-level fields (episode title, season #, episode #,
air date) can be applied on top of the show-level import.

Shown as a follow-up step after TMDBSearchDialog confirms which show --
same "always confirm explicitly, never auto-pick" principle applies here
too, since guessing the wrong episode writes wrong metadata just as
badly as guessing the wrong show.

`initial_season`/`initial_episode`, when given (typically parsed from the
file's own name via core.release_name_parser), pre-select that season and
episode so the common case is "glance and confirm" instead of hunting
through a season's episode list by hand -- the user still has to click
"Use Selected Episode" for anything to happen, so this never weakens the
"explicit confirmation" rule, it just saves the clicking.

NOTE: not runnable in this sandbox -- no PyQt6, no network. Syntax-
checked and reviewed only.
"""

from __future__ import annotations
from typing import Optional

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QComboBox, QListWidget,
    QListWidgetItem, QPushButton, QLabel, QTextEdit, QMessageBox,
)
from PyQt6.QtCore import Qt

from core.tmdb_client import get_tv_seasons, get_season_episodes, TMDBError, EpisodeInfo


class TVEpisodePickerDialog(QDialog):
    """Usage: dialog = TVEpisodePickerDialog(tmdb_id, parent=self);
    if dialog.exec(): season, episode = dialog.selected_season, dialog.selected_episode
    """

    def __init__(
        self, tmdb_id: int, initial_season: Optional[int] = None,
        initial_episode: Optional[int] = None, parent=None,
    ):
        super().__init__(parent)
        self.tmdb_id = tmdb_id
        self._initial_season = initial_season
        self._initial_episode = initial_episode
        self.selected_season: Optional[int] = None
        self.selected_episode: Optional[EpisodeInfo] = None

        self.setWindowTitle("Select Season & Episode")
        self.resize(450, 500)

        layout = QVBoxLayout(self)

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
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        button_row.addWidget(self.cancel_button)
        layout.addLayout(button_row)

        self._load_seasons()

    def _load_seasons(self) -> None:
        try:
            seasons = get_tv_seasons(self.tmdb_id)
        except TMDBError as e:
            QMessageBox.warning(self, "Could Not Load Seasons", str(e))
            self.reject()
            return

        if not seasons:
            QMessageBox.information(self, "No Seasons Found", "This show has no listed seasons on TMDB.")
            self.reject()
            return

        for season in seasons:
            self.season_combo.addItem(season.display_name, season.season_number)
        # currentIndexChanged fires from addItem above once a default
        # selection exists, loading episodes for the first season.

        if self._initial_season is not None:
            idx = self.season_combo.findData(self._initial_season)
            if idx >= 0:
                self.season_combo.setCurrentIndex(idx)  # re-fires _on_season_changed if it actually moved

    def _on_season_changed(self) -> None:
        season_number = self.season_combo.currentData()
        if season_number is None:
            return
        self.episode_list.clear()
        self.overview_box.clear()
        self.select_button.setEnabled(False)

        try:
            episodes = get_season_episodes(self.tmdb_id, season_number)
        except TMDBError as e:
            QMessageBox.warning(self, "Could Not Load Episodes", str(e))
            return

        select_row = -1
        for row, ep in enumerate(episodes):
            label = f"E{ep.episode_number}: {ep.name}" if ep.name else f"Episode {ep.episode_number}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, ep)
            self.episode_list.addItem(item)
            if (
                self._initial_episode is not None
                and season_number == self._initial_season
                and ep.episode_number == self._initial_episode
            ):
                select_row = row

        # Pre-select the episode parsed from the filename, if this is that
        # season -- still just a selection, not a confirmed pick; the user
        # still has to click "Use Selected Episode" (or double-click) for
        # it to take effect.
        if select_row >= 0:
            self.episode_list.setCurrentRow(select_row)

    def _on_episode_selection_changed(self) -> None:
        items = self.episode_list.selectedItems()
        if not items:
            self.select_button.setEnabled(False)
            self.overview_box.clear()
            return
        episode = items[0].data(Qt.ItemDataRole.UserRole)
        self.select_button.setEnabled(True)
        self.overview_box.setPlainText(episode.overview or "(no synopsis available)")

    def _on_accept(self) -> None:
        items = self.episode_list.selectedItems()
        if not items:
            return
        episode = items[0].data(Qt.ItemDataRole.UserRole)
        self.selected_season = self.season_combo.currentData()
        self.selected_episode = episode
        self.accept()
