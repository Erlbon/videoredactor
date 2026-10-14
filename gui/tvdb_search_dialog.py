"""
TVDBSearchDialog: candidate picker for TheTVDB metadata import -- the shared
search dialog (gui/tmdb_search_dialog.py's TMDBSearchDialog) with a TheTVDB
SearchSource.

TV-only (no movie mode, no Year box) -- TheTVDB is an alternative TV
metadata source alongside TMDB, not a movie source; TMDB remains the source
for movies unchanged.

Same "always shown, never auto-apply" principle as TMDBSearchDialog: the
user picks the specific right show every time, since a wrong pick writes
wrong metadata to the actual file.
"""

from __future__ import annotations

from core.tvdb_client import TVDBError, search_series
from gui.tmdb_search_dialog import SearchSource, TMDBSearchDialog


def _no_movies(query, year=None):
    return []


def _search(query, year=None):
    return search_series(query)  # looked up at call time (tests patch it); TheTVDB's search takes no year


TVDB_SOURCE = SearchSource(name="TheTVDB", movies=_no_movies, tv=_search, errors=(TVDBError,), year_box=False)


class TVDBSearchDialog(TMDBSearchDialog):
    """Search TheTVDB and let the user pick exactly one series.

    Usage: dialog = TVDBSearchDialog(initial_query=guess);
    if dialog.exec(): selected = dialog.selected_candidate
    """

    def __init__(self, initial_query: str = "", parent=None):
        super().__init__("tv", initial_query, parent=parent, source=TVDB_SOURCE)
