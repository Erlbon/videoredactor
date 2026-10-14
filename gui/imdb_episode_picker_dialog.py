"""
ImdbEpisodePickerDialog: the season + episode picker (gui/episode_picker_dialog.py)
over the LOCAL IMDb database, the offline counterpart of TVEpisodePickerDialog
(same `selected_season` / `selected_episode` interface, pre-selection from the
filename, nothing applied until the user confirms). IMDb's datasets have no
plot, so the detail box shows the episode's year, runtime and rating only.
"""

from __future__ import annotations

from typing import Optional

from core import imdb_local
from core.imdb_import import ImdbDatabaseError
from gui.episode_picker_dialog import EpisodePickerDialog, EpisodeSource


class ImdbEpisodeSource(EpisodeSource):
    window_title = "Select Season & Episode (IMDb Local Database)"
    errors = (ImdbDatabaseError,)
    empty_title = "No Episodes Found"
    empty_text = (
        "The local IMDb database has no episodes for this show (it may have been built without episodes, "
        "or without the title.episode file)."
    )
    no_overview = ""

    def __init__(self, db_path: str, series: int, series_name: str = ""):
        self.db_path = db_path
        self.series = series
        self.heading = series_name

    def _db(self):
        return imdb_local.open_database(self.db_path)

    def seasons(self, owner):
        return [
            (season, f"Specials ({count} episodes)" if season == 0 else f"Season {season} ({count} episodes)")
            for season, count in imdb_local.seasons_of(self._db(), self.series)
        ]

    def episodes(self, owner, season):
        return imdb_local.episodes_of(self._db(), self.series, season)


class ImdbEpisodePickerDialog(EpisodePickerDialog):
    """Usage: dialog = ImdbEpisodePickerDialog(db_path, series_tconst, series_name, parent=self);
    if dialog.exec(): season, episode = dialog.selected_season, dialog.selected_episode"""

    def __init__(
        self, db_path: str, series: int, series_name: str = "", initial_season: Optional[int] = None,
        initial_episode: Optional[int] = None, parent=None,
    ):
        super().__init__(ImdbEpisodeSource(db_path, series, series_name), parent, initial_season, initial_episode)
        self.db_path = db_path
        self.series = series
