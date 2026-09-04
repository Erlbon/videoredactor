"""
TVDBEpisodePickerDialog: pick a specific season + episode for a matched
TheTVDB series, so episode-level fields can be applied on top of the
show-level import.

Structurally different from tmdb_episode_picker_dialog.py in one way
that matches TheTVDB's actual API shape: TheTVDB has no per-season
episode-listing endpoint the way TMDB does, so this fetches the
series' FULL episode list once at dialog open, derives the season list
from that via core.tvdb_client.group_episodes_into_seasons(), and
filters the already-fetched episodes in memory when the season
selection changes -- no new network call per season, unlike the TMDB
dialog which does call out again for each season picked.

Same "always confirm explicitly, never auto-pick" principle as every
other picker dialog in this project.

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

from core.tvdb_client import (
    get_series_episodes, group_episodes_into_seasons, TVDBError, EpisodeInfo,
)


class TVDBEpisodePickerDialog(QDialog):
    """Usage: dialog = TVDBEpisodePickerDialog(tvdb_id, parent=self);
    if dialog.exec(): season, episode = dialog.selected_season, dialog.selected_episode
    """

    def __init__(self, tvdb_id: int, parent=None):
        super().__init__(parent)
        self.tvdb_id = tvdb_id
        self.selected_season: Optional[int] = None
        self.selected_episode: Optional[EpisodeInfo] = None
        self._all_episodes: list[EpisodeInfo] = []

        self.setWindowTitle("Select Season & Episode (TheTVDB)")
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

        self._load_episodes_and_seasons()

    def _load_episodes_and_seasons(self) -> None:
        try:
            self._all_episodes = get_series_episodes(self.tvdb_id)
        except TVDBError as e:
            QMessageBox.warning(self, "Could Not Load Episodes", str(e))
            self.reject()
            return

        seasons = group_episodes_into_seasons(self._all_episodes)
        if not seasons:
            QMessageBox.information(self, "No Episodes Found", "This series has no listed episodes on TheTVDB.")
            self.reject()
            return

        for season in seasons:
            self.season_combo.addItem(season.display_name, season.season_number)
        # currentIndexChanged fires from addItem above once a default
        # selection exists, populating episodes for the first season.

    def _on_season_changed(self) -> None:
        season_number = self.season_combo.currentData()
        if season_number is None:
            return
        self.episode_list.clear()
        self.overview_box.clear()
        self.select_button.setEnabled(False)

        # Filtering already-fetched data, not a new API call -- see
        # module docstring for why this differs from the TMDB picker.
        episodes = [ep for ep in self._all_episodes if ep.season_number == season_number]
        episodes.sort(key=lambda ep: ep.episode_number)

        for ep in episodes:
            label = f"E{ep.episode_number}: {ep.name}" if ep.name else f"Episode {ep.episode_number}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, ep)
            self.episode_list.addItem(item)

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
