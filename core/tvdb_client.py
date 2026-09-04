"""
TheTVDB (thetvdb.com) v4 API client -- a second, TV-focused metadata
source alongside TMDB. TMDB remains the source for movies; for TV,
the user can now choose between TMDB and TheTVDB when importing.

Auth is the one real structural difference from tmdb_client.py: TMDB
takes a simple api_key query parameter on every request, but TheTVDB's
v4 API requires a login step first (POST the API key, get back a JWT
bearer token) before any other endpoint will respond. That token is
cached in memory for the process's lifetime and only re-fetched if a
request comes back 401 (expired/invalid), rather than logging in on
every single call.

Reads the API key from the TVDB_API_KEY env var first (useful for
testing/CI without touching disk), falling back to settings.ini via
core/config.py -- same convention as tmdb_client.py and
opensubtitles_client.py before it. No key baked in, none requested from
the user in chat.

NOTE: not yet runnable/testable in this sandbox -- no network access.
Written against TheTVDB's documented v4 API
(https://thetvdb.github.io/v4-api/); needs a real functional pass once
a key + network access are available. Every function returns a clear
TVDBError (never a silent empty result) when the key is missing or
login fails, so the GUI layer can prompt the user rather than fail
mysteriously.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import urllib.request
import urllib.parse
import json
import os

from core.config import get_setting

BASE_URL = "https://api.thetvdb.com/v4"
# TheTVDB returns full, ready-to-use image URLs directly in its API
# responses (unlike TMDB's relative-path + separate base-URL scheme),
# so there's no equivalent IMAGE_BASE_URL constant needed here.

# Cached in-memory only -- never written to settings.ini. A login
# token is a short-lived credential (TheTVDB documents roughly a
# month), not a durable setting like the API key itself; keeping it
# out of the same store as the api_key means a corrupted/expired
# cached token can never end up looking like a configuration problem
# the user needs to fix by hand.
_cached_token: Optional[str] = None


class TVDBError(Exception):
    """Raised for missing API key, login failure, network failure, or
    a non-2xx response. Always carries a human-readable message meant
    to be shown directly to the user (e.g. in a QMessageBox), not just
    logged."""


@dataclass
class SeriesCandidate:
    tvdb_id: int
    name: str
    first_air_time: str   # 'YYYY-MM-DD' or '' if unknown
    overview: str
    image_url: Optional[str]  # full URL, ready to download directly

    @property
    def year(self) -> str:
        return self.first_air_time[:4] if self.first_air_time else ""


@dataclass
class EpisodeInfo:
    season_number: int
    episode_number: int
    name: str
    overview: str
    aired: str


@dataclass
class SeasonInfo:
    season_number: int
    episode_count: int

    @property
    def display_name(self) -> str:
        # TheTVDB uses season_number 0 for specials, same convention
        # TMDB uses -- labeled clearly rather than a confusing "Season 0",
        # matching tmdb_client.py's SeasonInfo.display_name exactly.
        if self.season_number == 0:
            return f"Specials ({self.episode_count} episodes)"
        return f"Season {self.season_number} ({self.episode_count} episodes)"


def group_episodes_into_seasons(episodes: list[EpisodeInfo]) -> list[SeasonInfo]:
    """Derive a season list from a flat episode list -- TheTVDB's API
    doesn't have a separate "list the seasons" endpoint the way this
    project's TMDB integration does; episodes already carry their own
    season_number, so the season list is just every distinct value
    with a count, sorted (specials/season 0 last, matching TMDB's own
    "specials shown after real seasons" convention rather than first).

    Pure function, no network -- kept separate from the API-calling
    code specifically so this logic is testable without a live
    connection, unlike almost everything else in this module.
    """
    counts: dict[int, int] = {}
    for ep in episodes:
        counts[ep.season_number] = counts.get(ep.season_number, 0) + 1

    numbered = sorted(n for n in counts if n != 0)
    ordered_numbers = numbered + ([0] if 0 in counts else [])
    return [SeasonInfo(season_number=n, episode_count=counts[n]) for n in ordered_numbers]


def get_api_key() -> Optional[str]:
    key = os.environ.get("TVDB_API_KEY")
    if key:
        return key
    key = get_setting("tvdb", "api_key")
    return key or None


def _require_api_key() -> str:
    key = get_api_key()
    if not key:
        raise TVDBError(
            "No TheTVDB API key configured. Set the TVDB_API_KEY "
            "environment variable, or add one under [tvdb] api_key in settings.ini."
        )
    return key


def _login(force: bool = False) -> str:
    """Return a cached bearer token, logging in fresh if there isn't
    one yet or `force=True` (used after a 401, meaning the cached
    token has expired or was invalidated).
    """
    global _cached_token
    if _cached_token and not force:
        return _cached_token

    api_key = _require_api_key()
    url = f"{BASE_URL}/login"
    payload = json.dumps({"apikey": api_key}).encode("utf-8")
    request = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise TVDBError("TheTVDB rejected the API key (401 Unauthorized).") from e
        raise TVDBError(f"TheTVDB login failed: HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise TVDBError(f"Could not reach TheTVDB: {e.reason}") from e

    token = data.get("data", {}).get("token")
    if not token:
        raise TVDBError("TheTVDB login succeeded but returned no token.")
    _cached_token = token
    return token


def _get_json(path: str, params: Optional[dict] = None, _retrying: bool = False) -> dict:
    """GET with the cached bearer token, retrying the login once (and
    only once, via _retrying) if the token's expired -- avoids both an
    infinite retry loop and forcing a fresh login on every single call.
    """
    token = _login()
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    url = f"{BASE_URL}{path}{query}"
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 401 and not _retrying:
            _login(force=True)
            return _get_json(path, params, _retrying=True)
        if e.code == 401:
            raise TVDBError("TheTVDB rejected the request even after re-login.") from e
        if e.code == 429:
            raise TVDBError("TheTVDB rate limit hit -- try again shortly.") from e
        raise TVDBError(f"TheTVDB request failed: HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise TVDBError(f"Could not reach TheTVDB: {e.reason}") from e


def search_series(query: str) -> list[SeriesCandidate]:
    """Search TheTVDB for TV series matching `query`. Always returns
    the full candidate list -- the picker dialog is responsible for
    letting the user choose, never this function auto-selecting a
    "best" match, same principle as tmdb_client.py's search_movies/
    search_tv (always show the picker, even for one strong match).
    """
    data = _get_json("/search", {"query": query, "type": "series"})
    results = []
    for r in data.get("data", []):
        results.append(SeriesCandidate(
            tvdb_id=int(r.get("tvdb_id") or r.get("id", 0)),
            name=r.get("name", ""),
            first_air_time=r.get("first_air_time", "") or "",
            overview=r.get("overview", "") or "",
            image_url=r.get("image_url"),
        ))
    return results


def get_series_details(series_id: int) -> dict:
    """Show-level details, returned as a dict of VideoMetadata-shaped
    field names -- caller applies these onto a VideoMetadata instance,
    matching tmdb_client.py's get_tv_show_details() return shape
    exactly so the GUI layer can treat either source's result the same way.
    """
    data = _get_json(f"/series/{series_id}/extended")
    series = data.get("data", {})
    genres = series.get("genres") or []
    return {
        "show_title": series.get("name", ""),
        "description": series.get("overview", "") or "",
        "genre_tags": ", ".join(g.get("name", "") for g in genres if isinstance(g, dict)),
        "release_date": series.get("firstAired", "") or "",
        "network": (series.get("originalNetwork") or {}).get("name", "") or "",
        "_poster_path": series.get("image"),  # TheTVDB gives a full URL here, not a relative path
    }


def get_series_episodes(series_id: int) -> list[EpisodeInfo]:
    """Every episode of a series, across all seasons -- TheTVDB has no
    separate per-season listing endpoint the way TMDB does, so this
    fetches the full episode list once; group_episodes_into_seasons()
    derives the season picker's list from this same data client-side.

    NOTE: only fetches the first page. TheTVDB's episodes endpoint is
    paginated for very long-running shows; a show with enough episodes
    to exceed one page would need `page` parameter handling added here
    -- flagged as a known gap rather than silently truncating long-
    running shows without mentioning it.
    """
    data = _get_json(f"/series/{series_id}/episodes/default")
    episodes_data = (data.get("data") or {}).get("episodes") or []
    return [
        EpisodeInfo(
            season_number=e.get("seasonNumber", 0),
            episode_number=e.get("number", 0),
            name=e.get("name", "") or "",
            overview=e.get("overview", "") or "",
            aired=e.get("aired", "") or "",
        )
        for e in episodes_data
    ]


def get_episode_details(series_id: int, season: int, episode: int) -> dict:
    """Episode-level details for a specific season/episode -- fetches
    the full episode list and filters client-side (same reasoning as
    get_series_episodes: no per-episode-lookup endpoint that's simpler
    than this), returned in the same shape as tmdb_client.py's
    get_tv_episode_details() so the GUI layer can treat either source
    identically.
    """
    episodes = get_series_episodes(series_id)
    for ep in episodes:
        if ep.season_number == season and ep.episode_number == episode:
            return {
                "title": ep.name,
                "description": ep.overview,
                "release_date": ep.aired,
                "season_number": season,
                "episode_number": episode,
            }
    raise TVDBError(f"Episode S{season}E{episode} not found for this series.")


def download_image(image_url: str) -> bytes:
    """Download poster/banner image bytes from a full TheTVDB image URL
    (unlike TMDB, TheTVDB's API responses already contain the complete
    URL, so there's no base-URL-plus-size-plus-path assembly needed
    here the way tmdb_client.py's download_poster() does)."""
    request = urllib.request.Request(image_url)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.read()
    except urllib.error.URLError as e:
        raise TVDBError(f"Could not download image: {e.reason}") from e
