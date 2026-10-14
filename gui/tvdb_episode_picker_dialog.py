"""
TVDBEpisodePickerDialog: the season + episode picker (gui/episode_picker_dialog.py)
for a matched TheTVDB series.

TheTVDB has no per-season episode-listing endpoint the way TMDB does, so
the source fetches the series' FULL episode list once at dialog open,
derives the season list from it via core.tvdb_client.group_episodes_into_seasons(),
and filters the already-fetched episodes in memory when the season changes
-- no new network call per season.
"""

from __future__ import annotations

from typing import Optional

from core.tvdb_client import EpisodeInfo, TVDBError, get_series_episodes, group_episodes_into_seasons
from gui.episode_picker_dialog import EpisodePickerDialog, EpisodeSource
from gui.lookup import run_lookup


class TvdbEpisodeSource(EpisodeSource):
    window_title = "Select Season & Episode (TheTVDB)"
    errors = (TVDBError,)
    seasons_error_title = "Could Not Load Episodes"  # the one load is the whole episode list
    empty_title = "No Episodes Found"
    empty_text = "This series has no listed episodes on TheTVDB."

    def __init__(self, tvdb_id: int):
        self.tvdb_id = tvdb_id
        self.all_episodes: list[EpisodeInfo] = []

    def seasons(self, owner):
        self.all_episodes = run_lookup(owner, get_series_episodes, self.tvdb_id)
        return [(s.season_number, s.display_name) for s in group_episodes_into_seasons(self.all_episodes)]

    def episodes(self, owner, season):
        episodes = [ep for ep in self.all_episodes if ep.season_number == season]
        return sorted(episodes, key=lambda ep: ep.episode_number)


class TVDBEpisodePickerDialog(EpisodePickerDialog):
    """Usage: dialog = TVDBEpisodePickerDialog(tvdb_id, parent=self);
    if dialog.exec(): season, episode = dialog.selected_season, dialog.selected_episode
    """

    def __init__(
        self, tvdb_id: int, initial_season: Optional[int] = None,
        initial_episode: Optional[int] = None, parent=None,
    ):
        super().__init__(TvdbEpisodeSource(tvdb_id), parent, initial_season, initial_episode)
        self.tvdb_id = tvdb_id
