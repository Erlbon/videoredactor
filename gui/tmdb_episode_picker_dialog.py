"""
TVEpisodePickerDialog: the season + episode picker (gui/episode_picker_dialog.py)
for a show matched on TMDB, shown as a follow-up step after TMDBSearchDialog
confirms which show. TMDB lists episodes per season, so each season picked
is a new request (under gui.lookup.run_lookup's busy dialog).
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import QMessageBox  # noqa: F401 -- tests patch picker.QMessageBox

from core.tmdb_client import TMDBError, get_season_episodes, get_tv_seasons
from gui.episode_picker_dialog import EpisodePickerDialog, EpisodeSource
from gui.lookup import run_lookup
from gui.tmdb_attribution import TmdbAttributionFooter


class TmdbEpisodeSource(EpisodeSource):
    errors = (TMDBError,)
    empty_text = "This show has no listed seasons on TMDB."

    def __init__(self, tmdb_id: int):
        self.tmdb_id = tmdb_id

    def seasons(self, owner):
        return [(s.season_number, s.display_name) for s in run_lookup(owner, get_tv_seasons, self.tmdb_id)]

    def episodes(self, owner, season):
        return run_lookup(owner, get_season_episodes, self.tmdb_id, season)

    def footer(self, parent):
        return TmdbAttributionFooter(parent)  # TMDB's required notice and logo


class TVEpisodePickerDialog(EpisodePickerDialog):
    """Usage: dialog = TVEpisodePickerDialog(tmdb_id, parent=self);
    if dialog.exec(): season, episode = dialog.selected_season, dialog.selected_episode
    """

    def __init__(
        self, tmdb_id: int, initial_season: Optional[int] = None,
        initial_episode: Optional[int] = None, parent=None,
    ):
        super().__init__(TmdbEpisodeSource(tmdb_id), parent, initial_season, initial_episode)
        self.tmdb_id = tmdb_id
