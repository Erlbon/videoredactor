"""Helpers for the Create Notes tests: video libraries made of empty files, and a fake network that answers TMDB and
Wikipedia requests from recorded-shape replies (nothing reaches the internet)."""

from __future__ import annotations

import json
import re
import urllib.error
from pathlib import Path

from core.notes_flow import Fetchers


class FakeFetch:
    """fetch(request) -> bytes. `routes` is a list of (regex, reply): the first route whose regex matches the URL answers;
    a reply of None is a 404, a callable gets the URL, an Exception instance is raised. Every URL asked is kept in `urls`."""

    def __init__(self, routes=()):
        self.routes = list(routes)
        self.urls: list[str] = []

    def __call__(self, request):
        url = request if isinstance(request, str) else request.full_url
        self.urls.append(url)
        for pattern, reply in self.routes:
            if re.search(pattern, url):
                if callable(reply):
                    reply = reply(url)
                if isinstance(reply, Exception):
                    raise reply
                if reply is None:
                    raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
                return json.dumps(reply).encode("utf-8")
        raise urllib.error.HTTPError(url, 404, "Not Found (no route)", {}, None)

    def asked(self, pattern: str) -> list[str]:
        return [u for u in self.urls if re.search(pattern, u)]


def touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def make_series(root: Path, name: str = "Breaking Bad (2008)", seasons: dict[int, int] | None = None,
                season_folders: bool = True) -> Path:
    """A series folder of empty episode files: {season: episode count}, in Season folders (or flat)."""
    seasons = seasons or {1: 7, 2: 13}
    for season, count in seasons.items():
        for episode in range(1, count + 1):
            file = f"Breaking.Bad.S{season:02d}E{episode:02d}.720p.mkv"
            touch(root / name / (f"Season {season}" if season_folders else "") / file)
    return root / name


def make_movie(root: Path, name: str = "The Matrix (1999)", file: str = "The.Matrix.1999.1080p.mkv") -> Path:
    touch(root / name / file)
    return root / name


# ---- TMDB and Wikipedia replies --------------------------------------------------------------------------------------

BREAKING_BAD = {
    "id": 1396, "name": "Breaking Bad", "original_name": "Breaking Bad", "first_air_date": "2008-01-20", "last_air_date": "2013-09-29",
    "status": "Ended", "overview": "A chemistry teacher turned drug manufacturer.", "episode_run_time": [47],
    "genres": [{"name": "Drama"}, {"name": "Crime"}], "networks": [{"name": "AMC"}], "origin_country": ["US"],
    "original_language": "en", "created_by": [{"name": "Vince Gilligan"}], "number_of_episodes": 62,
    "seasons": [{"season_number": 0, "episode_count": 9, "name": "Specials"}, {"season_number": 1, "episode_count": 7, "name": "Season 1"},
                {"season_number": 2, "episode_count": 13, "name": "Season 2"}, {"season_number": 3, "episode_count": 13}],
    "external_ids": {"imdb_id": "tt0903747"},
    "credits": {"cast": [{"name": "Bryan Cranston", "character": "Walter White"}, {"name": "Aaron Paul", "character": "Jesse Pinkman"}],
                "crew": []},
}
MATRIX = {
    "id": 603, "title": "The Matrix", "original_title": "The Matrix", "release_date": "1999-03-30", "status": "Released",
    "overview": "A hacker learns the truth.", "runtime": 136, "genres": [{"name": "Action"}, {"name": "Science Fiction"}],
    "production_countries": [{"name": "United States of America"}], "original_language": "en",
    "external_ids": {"imdb_id": "tt0133093"},
    "credits": {"cast": [{"name": "Keanu Reeves", "character": "Neo"}],
                "crew": [{"name": "Lana Wachowski", "job": "Director"}, {"name": "Lilly Wachowski", "job": "Director"},
                         {"name": "Lana Wachowski", "job": "Writer"}]},
}


def hit(kind, ident, name, date, overview=""):
    key = "name" if kind == "tv" else "title"
    date_key = "first_air_date" if kind == "tv" else "release_date"
    return {"id": ident, key: name, date_key: date, "overview": overview, "popularity": 10}


def tmdb_routes(extra=()):
    return [
        *extra,
        (r"/search/tv\?.*query=Breaking\+Bad", {"results": [hit("tv", 1396, "Breaking Bad", "2008-01-20")]}),
        (r"/tv/1396\?", BREAKING_BAD),
        (r"/search/movie\?.*query=The\+Matrix", {"results": [hit("movie", 603, "The Matrix", "1999-03-30"),
                                                             hit("movie", 604, "The Matrix Reloaded", "2003-05-15")]}),
        (r"/movie/603\?", MATRIX),
    ]


def wiki_routes(extra=()):
    def summary(title, text):
        return {"title": title, "extract": text, "description": "tv series", "type": "standard",
                "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/" + title.replace(" ", "_")}}}

    return [
        *extra,
        (r"haswbstatement:P345=tt0903747|haswbstatement%3AP345%3Dtt0903747", {"query": {"search": [{"title": "Q1079"}]}}),
        (r"haswbstatement:P345=tt0133093|haswbstatement%3AP345%3Dtt0133093", {"query": {"search": [{"title": "Q83495"}]}}),
        (r"ids=Q1079", {"entities": {"Q1079": {"sitelinks": {"enwiki": {"title": "Breaking Bad"}}}}}),
        (r"ids=Q83495", {"entities": {"Q83495": {"sitelinks": {"enwiki": {"title": "The Matrix"}}}}}),
        (r"page/summary/Breaking_Bad", summary("Breaking Bad", "Breaking Bad is an American crime drama television series. It ran for five seasons.")),
        (r"page/summary/The_Matrix", summary("The Matrix", "The Matrix is a 1999 science fiction action film.")),
    ]


def fake_fetchers(tmdb_extra=(), wiki_extra=(), key="TEST-KEY", wikipedia=True) -> Fetchers:
    return Fetchers(tmdb=FakeFetch(tmdb_routes(tmdb_extra)), wiki=FakeFetch(wiki_routes(wiki_extra)), wikipedia=wikipedia, key=key)
