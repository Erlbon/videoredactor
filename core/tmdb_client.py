"""
TMDB (The Movie Database) API client.

Reads the API key from the TMDB_API_KEY env var first (useful for
testing/CI without touching disk), falling back to settings.ini via
core/config.py. No key baked in, none requested from the user in chat.

NOTE: not yet runnable/testable in this sandbox -- no network access.
Written against TMDB's documented v3 API
(https://developer.themoviedb.org/reference); needs a real functional
pass once a key + network access are available. Every function returns
a clear TMDBError (never a silent empty result) when the key is missing,
so the GUI layer can prompt the user rather than fail mysteriously.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import urllib.request
import urllib.parse
import json

from core.config import get_setting
import os

BASE_URL = "https://api.themoviedb.org/3"
IMAGE_BASE_URL = "https://image.tmdb.org/t/p/"
POSTER_SIZE = "w500"  # reasonable balance of quality vs download size


class TMDBError(Exception):
    """Raised for missing API key, network failure, or a non-2xx response.
    Always carries a human-readable message meant to be shown directly to
    the user (e.g. in a QMessageBox), not just logged."""


@dataclass
class MovieCandidate:
    tmdb_id: int
    title: str
    release_date: str   # 'YYYY-MM-DD' or '' if unreleased/unknown
    overview: str
    poster_path: Optional[str]  # relative path; combine with IMAGE_BASE_URL

    @property
    def year(self) -> str:
        return self.release_date[:4] if self.release_date else ""


@dataclass
class TVCandidate:
    tmdb_id: int
    name: str
    first_air_date: str
    overview: str
    poster_path: Optional[str]

    @property
    def year(self) -> str:
        return self.first_air_date[:4] if self.first_air_date else ""


@dataclass
class SeasonInfo:
    season_number: int
    name: str
    episode_count: int

    @property
    def display_name(self) -> str:
        # TMDB uses season_number 0 for specials -- label it clearly
        # rather than showing a confusing "Season 0".
        if self.season_number == 0:
            return f"Specials ({self.episode_count} episodes)"
        return f"{self.name} ({self.episode_count} episodes)"


@dataclass
class EpisodeInfo:
    episode_number: int
    name: str
    overview: str
    air_date: str


def get_api_key() -> Optional[str]:
    key = os.environ.get("TMDB_API_KEY")
    if key:
        return key
    key = get_setting("tmdb", "api_key")
    return key or None


def _require_api_key() -> str:
    key = get_api_key()
    if not key:
        raise TMDBError(
            "No TMDB API key configured. Set the TMDB_API_KEY environment "
            "variable, or add one under [tmdb] api_key in settings.ini."
        )
    return key


def _get_json(path: str, params: dict) -> dict:
    key = _require_api_key()
    params = {**params, "api_key": key}
    url = f"{BASE_URL}{path}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise TMDBError("TMDB rejected the API key (401 Unauthorized).") from e
        if e.code == 429:
            raise TMDBError("TMDB rate limit hit -- try again shortly.") from e
        raise TMDBError(f"TMDB request failed: HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise TMDBError(f"Could not reach TMDB: {e.reason}") from e


def search_movies(query: str, year: Optional[str] = None) -> list[MovieCandidate]:
    """Search TMDB for movies matching `query`, optionally narrowed by
    release year. Always returns the full candidate list -- the picker
    dialog is responsible for letting the user choose, never this
    function auto-selecting a 'best' match (per explicit instruction:
    always show the picker, even for one strong match).
    """
    params = {"query": query, "include_adult": "false"}
    if year:
        params["year"] = year
    data = _get_json("/search/movie", params)
    return [
        MovieCandidate(
            tmdb_id=r["id"],
            title=r.get("title", ""),
            release_date=r.get("release_date", ""),
            overview=r.get("overview", ""),
            poster_path=r.get("poster_path"),
        )
        for r in data.get("results", [])
    ]


def search_tv(query: str) -> list[TVCandidate]:
    """Search TMDB for TV shows matching `query` (show-level search --
    season/episode lookup is a separate step once a show is picked)."""
    data = _get_json("/search/tv", {"query": query})
    return [
        TVCandidate(
            tmdb_id=r["id"],
            name=r.get("name", ""),
            first_air_date=r.get("first_air_date", ""),
            overview=r.get("overview", ""),
            poster_path=r.get("poster_path"),
        )
        for r in data.get("results", [])
    ]


def get_movie_details(tmdb_id: int) -> dict:
    """Full movie detail fetch, returned as a dict of VideoMetadata-shaped
    field names -- caller applies these onto a VideoMetadata instance
    (only overwriting fields the user confirms, same "only touched fields"
    principle as TagPanel's bulk-edit apply).
    """
    data = _get_json(f"/movie/{tmdb_id}", {"append_to_response": "credits"})

    director = ""
    for crew in data.get("credits", {}).get("crew", []):
        if crew.get("job") == "Director":
            director = crew.get("name", "")
            break

    cast_names = [c.get("name", "") for c in data.get("credits", {}).get("cast", [])[:10]]

    return {
        "title": data.get("title", ""),
        "description": data.get("overview", ""),
        "genre_tags": ", ".join(g.get("name", "") for g in data.get("genres", [])),
        "release_date": data.get("release_date", ""),
        "language": data.get("original_language", ""),
        "director": director,
        "cast": ", ".join(cast_names),
        "studio": ", ".join(c.get("name", "") for c in data.get("production_companies", [])),
        "_poster_path": data.get("poster_path"),  # underscore: not a VideoMetadata field, handled specially by caller
    }


def get_tv_show_details(tmdb_id: int) -> dict:
    """Show-level details (Network, general info) -- season/episode
    numbering is filled in separately since it's per-file, not per-show.
    """
    data = _get_json(f"/tv/{tmdb_id}", {})
    return {
        "show_title": data.get("name", ""),
        "description": data.get("overview", ""),
        "genre_tags": ", ".join(g.get("name", "") for g in data.get("genres", [])),
        "release_date": data.get("first_air_date", ""),
        "network": ", ".join(n.get("name", "") for n in data.get("networks", [])),
        "_poster_path": data.get("poster_path"),
    }


def get_tv_seasons(tmdb_id: int) -> list[SeasonInfo]:
    """List a show's seasons, for the season/episode picker. Reuses the
    same /tv/{id} endpoint as get_tv_show_details (TMDB includes a
    'seasons' array in that response) rather than a second round-trip --
    called separately from get_tv_show_details anyway since the picker
    needs this before the user has necessarily committed to importing
    show-level fields.
    """
    data = _get_json(f"/tv/{tmdb_id}", {})
    return [
        SeasonInfo(
            season_number=s.get("season_number", 0),
            name=s.get("name", f"Season {s.get('season_number', '?')}"),
            episode_count=s.get("episode_count", 0),
        )
        for s in data.get("seasons", [])
    ]


def get_season_episodes(tmdb_id: int, season_number: int) -> list[EpisodeInfo]:
    """List episodes within a specific season, for the episode picker."""
    data = _get_json(f"/tv/{tmdb_id}/season/{season_number}", {})
    return [
        EpisodeInfo(
            episode_number=e.get("episode_number", 0),
            name=e.get("name", ""),
            overview=e.get("overview", ""),
            air_date=e.get("air_date", ""),
        )
        for e in data.get("episodes", [])
    ]


def get_tv_episode_details(tmdb_id: int, season: int, episode: int) -> dict:
    """Episode-level details for a specific season/episode of a show
    already identified via get_tv_show_details."""
    data = _get_json(f"/tv/{tmdb_id}/season/{season}/episode/{episode}", {})
    return {
        "title": data.get("name", ""),           # episode title
        "description": data.get("overview", ""),
        "release_date": data.get("air_date", ""),
        "season_number": season,
        "episode_number": episode,
    }


def download_poster(poster_path: str) -> bytes:
    """Download poster image bytes at POSTER_SIZE resolution."""
    url = f"{IMAGE_BASE_URL}{POSTER_SIZE}{poster_path}"
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            return response.read()
    except urllib.error.URLError as e:
        raise TMDBError(f"Could not download poster: {e.reason}") from e
