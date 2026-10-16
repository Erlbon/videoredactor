"""
core/notes_tmdb.py

The TMDB reads the folder notes need: a search for a series or a film, and the details with cast and the external ids
(the IMDb id is what links a title to its Wikipedia article through Wikidata). Qt-free; `fetch` is the injectable
`fetch(request) -> bytes` (the app's real one, wrapped in the notes cache, or a recorded reply in a test).

The API key is the user's own (Tools > API Keys, or TMDB_API_KEY): it travels as the `api_key` query parameter, which the
notes cache is told NOT to store (CachedFetch(secret_params=("api_key",))), and fetch_json removes URL query strings from
every error message.

TMDB asks for attribution: a note built from it says so (core/notes_render.py: TMDB_CREDIT).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from core.tmdb_client import BASE_URL, USER_AGENT, TMDBError, get_api_key
from redactor_common.core.lookup_client import build_request, fetch_json, make_default_fetch

SEARCH_LIMIT = 8
CAST_LIMIT = 8
_STATUS_MESSAGES = {
    401: "TMDB rejected the API key (401 Unauthorized).",
    429: "TMDB rate limit hit -- try again shortly.",
}


@dataclass
class TitleHit:
    """One search result: a series (kind 'tv') or a film (kind 'movie')."""

    kind: str
    id: int
    name: str
    original_name: str = ""
    date: str = ""  # first air date / release date, 'YYYY-MM-DD' or ''
    overview: str = ""
    popularity: float = 0.0

    @property
    def year(self) -> str:
        return self.date[:4]

    @property
    def names(self) -> list[str]:
        return [n for n in (self.name, self.original_name) if n]


@dataclass
class CastMember:
    name: str
    character: str = ""


@dataclass
class SeasonInfo:
    number: int
    episodes: int
    name: str = ""


@dataclass
class TitleDetails:
    kind: str
    id: int
    name: str
    original_name: str = ""
    date: str = ""
    end_date: str = ""  # a series' last air date
    overview: str = ""
    tagline: str = ""
    genres: list[str] = field(default_factory=list)
    status: str = ""
    runtime: int = 0  # a film's minutes, a series' typical episode minutes
    networks: list[str] = field(default_factory=list)
    countries: list[str] = field(default_factory=list)
    language: str = ""
    creators: list[str] = field(default_factory=list)  # a series
    directors: list[str] = field(default_factory=list)  # a film
    writers: list[str] = field(default_factory=list)
    cast: list[CastMember] = field(default_factory=list)
    seasons: list[SeasonInfo] = field(default_factory=list)
    episode_count: int = 0
    imdb_id: str = ""
    homepage: str = ""

    @property
    def year(self) -> str:
        return self.date[:4]

    @property
    def url(self) -> str:
        return f"https://www.themoviedb.org/{self.kind}/{self.id}"


def make_fetch() -> Callable:
    """The real fetch(request) -> bytes with this app's User-Agent."""
    return make_default_fetch(USER_AGENT, timeout=15)


class Tmdb:
    """TMDB for the notes. One instance per run; `fetch` should be the cached one."""

    def __init__(self, fetch: Callable, key: Optional[str] = None):
        self._fetch = fetch
        self._key = key

    def _get(self, path: str, params: dict) -> dict:
        key = self._key or get_api_key()
        if not key:
            raise TMDBError("No TMDB API key configured. Add one under Tools > API Keys... (or set the TMDB_API_KEY "
                            "environment variable).")
        request = build_request(f"{BASE_URL}{path}", {**params, "api_key": key})
        return fetch_json(request, self._fetch, TMDBError, "TMDB", status_messages=_STATUS_MESSAGES,
                          secrets=[key], ignore_404=True) or {}

    def search(self, kind: str, query: str, year: str = "") -> list[TitleHit]:
        """Hits for `query` ('tv' or 'movie'), best first (TMDB's own order), at most SEARCH_LIMIT."""
        if kind == "tv":
            params = {"query": query}
            if year:
                params["first_air_date_year"] = year
        else:
            params = {"query": query, "include_adult": "false"}
            if year:
                params["year"] = year
        data = self._get(f"/search/{kind}", params)
        hits = []
        for r in (data.get("results") or [])[:SEARCH_LIMIT]:
            if not isinstance(r, dict) or "id" not in r:
                continue
            hits.append(TitleHit(
                kind=kind, id=int(r["id"]), name=str(r.get("name") or r.get("title") or ""),
                original_name=str(r.get("original_name") or r.get("original_title") or ""),
                date=str(r.get("first_air_date") or r.get("release_date") or ""),
                overview=str(r.get("overview") or ""), popularity=float(r.get("popularity") or 0)))
        return hits

    def details(self, kind: str, tmdb_id: int) -> Optional[TitleDetails]:
        """The details with credits and external ids, or None when TMDB has no such title."""
        data = self._get(f"/{kind}/{tmdb_id}", {"append_to_response": "credits,external_ids"})
        if not data or "id" not in data:
            return None
        credits = data.get("credits") or {}
        crew = [c for c in credits.get("crew") or [] if isinstance(c, dict)]
        # in BILLING order (TMDB's own `order`), the lead first: the list comes back in no promised order
        billed = sorted((c for c in credits.get("cast") or [] if isinstance(c, dict) and c.get("name")),
                        key=lambda c: c.get("order") if isinstance(c.get("order"), int) else 10_000)
        cast = [CastMember(str(c.get("name")), str(c.get("character") or "")) for c in billed[:CAST_LIMIT]]
        is_tv = kind == "tv"
        runtimes = data.get("episode_run_time") or []
        details = TitleDetails(
            kind=kind, id=int(data["id"]), name=str(data.get("name") or data.get("title") or ""),
            original_name=str(data.get("original_name") or data.get("original_title") or ""),
            date=str(data.get("first_air_date") or data.get("release_date") or ""),
            end_date=str(data.get("last_air_date") or "") if str(data.get("status") or "") in ("Ended", "Canceled") else "",
            overview=str(data.get("overview") or ""), tagline=str(data.get("tagline") or ""),
            genres=[str(g.get("name")) for g in data.get("genres") or [] if isinstance(g, dict) and g.get("name")],
            status=str(data.get("status") or ""),
            runtime=int(data.get("runtime") or (runtimes[0] if runtimes else 0) or 0),
            networks=[str(n.get("name")) for n in data.get("networks") or [] if isinstance(n, dict) and n.get("name")],
            countries=[str(c) for c in (data.get("origin_country") or [])]
            or [str(c.get("name")) for c in data.get("production_countries") or [] if isinstance(c, dict) and c.get("name")],
            language=str(data.get("original_language") or ""),
            creators=[str(c.get("name")) for c in data.get("created_by") or [] if isinstance(c, dict) and c.get("name")] if is_tv else [],
            directors=[str(c.get("name")) for c in crew if c.get("job") == "Director" and c.get("name")][:3],
            writers=[str(c.get("name")) for c in crew if c.get("job") in ("Writer", "Screenplay") and c.get("name")][:3],
            cast=cast,
            seasons=[SeasonInfo(int(s.get("season_number") or 0), int(s.get("episode_count") or 0), str(s.get("name") or ""))
                     for s in data.get("seasons") or [] if isinstance(s, dict)] if is_tv else [],
            episode_count=int(data.get("number_of_episodes") or 0),
            imdb_id=str((data.get("external_ids") or {}).get("imdb_id") or data.get("imdb_id") or ""),
            homepage=str(data.get("homepage") or ""),
        )
        return details

    def season_air_dates(self, tmdb_id: int, season: int) -> dict[int, str]:
        """{episode number: air date 'YYYY-MM-DD'} of one season of a series (one request; {} when TMDB has none)."""
        data = self._get(f"/tv/{tmdb_id}/season/{season}", {})
        return {int(e["episode_number"]): str(e.get("air_date") or "") for e in data.get("episodes") or []
                if isinstance(e, dict) and "episode_number" in e}
