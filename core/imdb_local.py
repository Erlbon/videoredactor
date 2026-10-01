"""
core/imdb_local.py

Looks films, series and episodes up in the OFFLINE IMDb database that
core/imdb_import.py builds (Tools > IMDb Database...): no network, no rate
limit, milliseconds per query. Qt-free.

What IMDb's datasets can and cannot give (the dialog and the Redact step
say so too): title, original title, year (start year for a series),
runtime, genres, rating and vote count, and for episodes the series,
season number, episode number and episode title. NO plot, poster, cast,
director or studio -- those still come from TMDB/TheTVDB. Nothing here
touches the network.

Results are shaped like the online lookups' (core/tmdb_client's
MovieCandidate / TVCandidate / EpisodeInfo, as subclasses that carry the
IMDb extras), so gui/tmdb_search_dialog.py's flow and the Redact lookup
step can treat both alike. `exact` on a candidate says the searched title
equals the film's title, original title or a stored alternative title
(so "Il nome della rosa" finds The Name of the Rose); ranking is exact
first, then closest year (a film often differs by a year between festival
and release), then most votes.

Field mapping (see movie_fields / show_fields / episode_fields):
    video field        <- IMDb
    title              <- primary title (film) / episode title (episode)
    release_date       <- the year, e.g. "1984" (series: the first year;
                          episode: its year). Year only: a fuller date
                          from TMDB wins when both are available.
    genre_tags         <- genres, comma-separated; "Sci-Fi" -> "Science
                          Fiction", "Talk-Show" -> "Talk", "Reality-TV" ->
                          "Reality", "Game-Show" -> "Game Show"; "Adult" dropped
    show_title         <- the series' primary title
    season_number,
    episode_number     <- the episode's numbers
    (runtime, rating and votes are shown in the picker only: the app has
     no field for them; the IMDb id has none either, but an id written in
     the filename or the Comment field ("tt0087182") is used to find the title)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

from redactor_common.core.local_db import (
    LocalDatabase,
    fts_match_string,
    normalize_words,
    open_cached,
    year_gap,
)

from core import imdb_import
from core.imdb_import import ImdbDatabaseError, imdb_id, tconst_number
from core.tmdb_client import EpisodeInfo, MovieCandidate, TVCandidate

SERVICE_NAME = "IMDb (local database)"
KIND = "an IMDb lookup database built by this app (Tools > IMDb Database)"
MOVIE_KINDS = ("movie", "tvMovie", "video", "tvSpecial", "short", "tvShort")
SERIES_KINDS = ("tvSeries", "tvMiniSeries")
POOL = 120  # rows pulled from the full-text index before ranking

ATTRIBUTION = imdb_import.ATTRIBUTION  # IMDb's required acknowledgement, shown wherever its data is

NO_PLOT_NOTE = ("IMDb's datasets hold no plot, poster or cast: the local database fills the title, year, genres, "
                "and episode data only. Everything else stays with TMDB.")

_GENRE_MAP = {"Sci-Fi": "Science Fiction", "Talk-Show": "Talk", "Reality-TV": "Reality", "Game-Show": "Game Show"}
_DROP_GENRES = {"Adult"}
_ID_RE = re.compile(r"(?<![A-Za-z0-9])tt(\d{7,9})(?!\d)")


class ImdbLocalError(ImdbDatabaseError):
    """The local IMDb database is missing, unreadable or not the right kind of file."""


class ImdbLocalDatabase(LocalDatabase):
    def __init__(self, path: str):
        super().__init__(path, ("titles",), KIND, ImdbLocalError)
        self.has_fts = self.has_table("titles_fts")
        self.has_akas = self.has_table("akas") and self.has_table("akas_fts")
        self.has_episodes = self.has_table("episodes")


def open_database(path: str) -> ImdbLocalDatabase:
    """The session's one opened database for `path`."""
    return open_cached(path, ImdbLocalDatabase)


def database_ready(path: Optional[str]) -> str:
    """"" when `path` can be queried, else a short reason (for a note or hint)."""
    if not path:
        return "no local IMDb database is set up (Tools > IMDb Database)"
    try:
        open_database(path)
    except ImdbDatabaseError as exc:
        return str(exc)
    return ""


# --- candidates ---------------------------------------------------------------------------------


def map_genres(text: str) -> str:
    """IMDb's "Action,Sci-Fi" as this app's comma-separated genre text."""
    out: list[str] = []
    for genre in (text or "").split(","):
        genre = genre.strip()
        if not genre or genre in _DROP_GENRES:
            continue
        genre = _GENRE_MAP.get(genre, genre)
        if genre not in out:
            out.append(genre)
    return ", ".join(out)


def _summary(kind: str, year, end_year, runtime, genres: str, rating, votes, tconst: int, alias: str = "") -> str:
    parts = [f"IMDb {imdb_id(tconst)}", kind]
    if alias:
        parts.append(f"also known as '{alias}'")
    if year:
        parts.append(f"{year}-{end_year}" if end_year and end_year != year else str(year))
    if runtime:
        parts.append(f"{runtime} min")
    if genres:
        parts.append(map_genres(genres))
    if rating:
        parts.append(f"rated {rating:.1f}" + (f" ({votes:,} votes)" if votes else ""))
    return " | ".join(p for p in parts if p)


@dataclass
class ImdbMovieCandidate(MovieCandidate):
    """A film-like title (movie, TV movie, video, special, short). tmdb_id is 0: it has none."""

    imdb_id: str = ""
    kind: str = ""
    original_title: str = ""
    runtime: Optional[int] = None
    genres: str = ""
    rating: Optional[float] = None
    votes: Optional[int] = None
    exact: bool = False
    alias: str = ""  # the alternative title that matched, when the title itself didn't

    @property
    def tconst(self) -> int:
        return int(self.imdb_id[2:])


@dataclass
class ImdbTVCandidate(TVCandidate):
    imdb_id: str = ""
    kind: str = ""
    original_title: str = ""
    end_year: Optional[int] = None
    runtime: Optional[int] = None
    genres: str = ""
    rating: Optional[float] = None
    votes: Optional[int] = None
    exact: bool = False
    alias: str = ""

    @property
    def tconst(self) -> int:
        return int(self.imdb_id[2:])


@dataclass
class ImdbEpisodeInfo(EpisodeInfo):
    """An episode: episode_number/name/air_date as the TMDB picker expects (overview carries the
    runtime and rating line; air_date is the year)."""

    imdb_id: str = ""
    series: int = 0
    season: int = 0
    runtime: Optional[int] = None
    rating: Optional[float] = None
    votes: Optional[int] = None
    year: Optional[int] = None


_TITLE_COLUMNS = "tconst, kind, title, original_title, year, end_year, runtime, genres, rating, votes"


def _candidate(row: tuple, exact: bool, alias: str):
    tconst, kind, title, original, year, end_year, runtime, genres, rating, votes = row
    overview = _summary(kind, year, end_year, runtime, genres or "", rating, votes, tconst, alias)
    common = dict(imdb_id=imdb_id(tconst), kind=kind, original_title=original or "", runtime=runtime,
                  genres=genres or "", rating=rating, votes=votes, exact=exact, alias=alias)
    if kind in SERIES_KINDS:
        return ImdbTVCandidate(0, title or "", str(year) if year else "", overview, None, end_year=end_year, **common)
    return ImdbMovieCandidate(0, title or "", str(year) if year else "", overview, None, **common)


# --- searching ----------------------------------------------------------------------------------


def _chunks(items: list, size: int = 500) -> Iterable[list]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _fts_ids(db: ImdbLocalDatabase, text: str, kinds: tuple[str, ...], prefix: bool) -> tuple[list[int], dict[int, str]]:
    """tconsts whose title / original title (and, via the alias index, alternative title)
    contains every word of `text`, best match first; plus tconst -> matching alias text."""
    match = fts_match_string(text, prefix=prefix)
    if not match:
        return [], {}
    marks = ",".join("?" * len(kinds))
    ids = [r[0] for r in db.query(
        f"select t.tconst from titles_fts f join titles t on t.tconst = f.rowid "
        f"where titles_fts match ? and t.kind in ({marks}) order by f.rank limit ?", (match, *kinds, POOL))]
    aliases: dict[int, str] = {}
    if db.has_akas:
        for tconst, title in db.query(
            f"select a.tconst, a.title from akas_fts f join akas a on a.rowid = f.rowid join titles t on t.tconst = a.tconst "
            f"where akas_fts match ? and t.kind in ({marks}) order by f.rank limit ?", (match, *kinds, POOL),
        ):
            aliases.setdefault(tconst, title)
            if tconst not in ids:
                ids.append(tconst)
    return ids, aliases


def _search(db: ImdbLocalDatabase, title: str, year: Optional[str], kinds: tuple[str, ...], limit: int) -> list:
    title = (title or "").strip()
    if not title:
        raise ImdbLocalError("A title is required to search the IMDb database.")
    if not db.has_fts:
        raise ImdbLocalError("This IMDb database has no full-text index -- rebuild it under Tools > IMDb Database.")
    wanted = normalize_words(title)
    ids, aliases = _fts_ids(db, title, kinds, prefix=False)
    if not ids:
        ids, aliases = _fts_ids(db, title, kinds, prefix=True)  # "dun" -> Dune
    if not ids:
        return []
    rows: dict[int, tuple] = {}
    for chunk in _chunks(ids):
        marks = ",".join("?" * len(chunk))
        for row in db.query(f"select {_TITLE_COLUMNS} from titles where tconst in ({marks})", chunk):
            rows[row[0]] = row
    alias_norms: dict[int, list[tuple[str, str]]] = {}
    if db.has_akas:
        for chunk in _chunks(list(rows)):
            marks = ",".join("?" * len(chunk))
            for tconst, norm, raw in db.query(f"select tconst, norm, title from akas where tconst in ({marks})", chunk):
                alias_norms.setdefault(tconst, []).append((norm, raw))
    scored = []
    for tconst, row in rows.items():
        _t, _k, primary, original, row_year, *_rest = row
        exact_alias = next((raw for norm, raw in alias_norms.get(tconst, []) if norm == wanted), "")
        exact_title = wanted in (normalize_words(primary or ""), normalize_words(original or ""))
        exact = exact_title or bool(exact_alias)
        alias = "" if exact_title else (exact_alias or aliases.get(tconst, ""))
        gap = year_gap(year, row_year) if year and row_year else (0 if not year else 9999)
        scored.append(((not exact, gap, -(row[9] or 0), tconst), _candidate(row, exact, alias)))
    scored.sort(key=lambda pair: pair[0])
    return [candidate for _key, candidate in scored[:limit]]


def search_movies(db: ImdbLocalDatabase, title: str, year: Optional[str] = None, limit: int = 12) -> list[ImdbMovieCandidate]:
    """Film-like titles matching `title` (also by an alternative or original title), exact title
    first, then nearest year (a year off by one still ranks right after the exact year), then most votes."""
    return _search(db, title, year, MOVIE_KINDS, limit)


def search_series(db: ImdbLocalDatabase, title: str, year: Optional[str] = None, limit: int = 12) -> list[ImdbTVCandidate]:
    """TV series / miniseries matching `title`, ranked like search_movies()."""
    return _search(db, title, year, SERIES_KINDS, limit)


# --- ids ----------------------------------------------------------------------------------------


def find_imdb_id(*texts: Optional[str]) -> str:
    """The first IMDb title id ("tt0087182") written in any of the texts -- a filename like
    "Dune (1984) {imdb-tt0087182}" or a Comment with an imdb.com/title/ URL -- else ""."""
    for text in texts:
        match = _ID_RE.search(text or "")
        if match:
            return imdb_id(int(match.group(1)))
    return ""


def title_by_id(db: ImdbLocalDatabase, text: str):
    """The film, series or episode with this IMDb id ("tt0087182", or just the digits), or None.
    A film/series comes back as its candidate (exact=True); an episode as an ImdbEpisodeInfo."""
    found = find_imdb_id(text) or (imdb_id(int(text)) if (text or "").strip().isdigit() else "")
    number = tconst_number(found)
    if number is None:
        return None
    row = db.query_one(f"select {_TITLE_COLUMNS} from titles where tconst = ?", (number,))
    if row is not None:
        return _candidate(row, True, "")
    if db.has_episodes:
        ep = db.query_one(
            "select tconst, parent, season, episode, title, year, runtime, rating, votes from episodes where tconst = ?",
            (number,),
        )
        if ep is not None:
            return _episode(ep)
    return None


def series_by_id(db: ImdbLocalDatabase, series: int) -> Optional[ImdbTVCandidate]:
    row = db.query_one(f"select {_TITLE_COLUMNS} from titles where tconst = ?", (series,))
    candidate = _candidate(row, True, "") if row else None
    return candidate if isinstance(candidate, ImdbTVCandidate) else None


# --- episodes -----------------------------------------------------------------------------------

_EPISODE_COLUMNS = "tconst, parent, season, episode, title, year, runtime, rating, votes"


def _episode(row: tuple) -> ImdbEpisodeInfo:
    tconst, parent, season, episode, title, year, runtime, rating, votes = row
    bits = [f"IMDb {imdb_id(tconst)}", f"season {season}, episode {episode}" if season is not None else ""]
    if year:
        bits.append(str(year))
    if runtime:
        bits.append(f"{runtime} min")
    if rating:
        bits.append(f"rated {rating:.1f}" + (f" ({votes:,} votes)" if votes else ""))
    return ImdbEpisodeInfo(
        episode_number=episode or 0, name=title or "", overview=" | ".join(b for b in bits if b),
        air_date=str(year) if year else "", imdb_id=imdb_id(tconst), series=parent, season=season or 0,
        runtime=runtime, rating=rating, votes=votes, year=year,
    )


def find_episode(db: ImdbLocalDatabase, series: int, season: int, episode: int) -> Optional[ImdbEpisodeInfo]:
    """The episode `episode` of season `season` of the series with tconst `series`, or None (also when
    the database was built without episodes)."""
    if not db.has_episodes:
        return None
    rows = db.query(
        f"select {_EPISODE_COLUMNS} from episodes where parent = ? and season = ? and episode = ? "
        "order by votes desc", (series, season, episode))
    return _episode(rows[0]) if rows else None


def seasons_of(db: ImdbLocalDatabase, series: int) -> list[tuple[int, int]]:
    """[(season, episode count)] of a series, season order (season 0 = specials)."""
    if not db.has_episodes:
        return []
    return [(s, n) for s, n in db.query(
        "select season, count(*) from episodes where parent = ? and season is not null group by season order by season",
        (series,))]


def episodes_of(db: ImdbLocalDatabase, series: int, season: int) -> list[ImdbEpisodeInfo]:
    if not db.has_episodes:
        return []
    return [_episode(r) for r in db.query(
        f"select {_EPISODE_COLUMNS} from episodes where parent = ? and season = ? order by episode", (series, season))]


# --- mapping to the video's fields --------------------------------------------------------------


def movie_fields(candidate: ImdbMovieCandidate) -> dict:
    """VideoMetadata field names -> values for a film (empty values left out)."""
    fields = {"title": candidate.title, "release_date": candidate.release_date,
              "genre_tags": map_genres(candidate.genres)}
    return {k: v for k, v in fields.items() if v}


def show_fields(candidate: ImdbTVCandidate) -> dict:
    """Show-level fields: show title, genres, first year (as release_date)."""
    fields = {"show_title": candidate.name, "genre_tags": map_genres(candidate.genres),
              "release_date": candidate.first_air_date}
    return {k: v for k, v in fields.items() if v}


def episode_fields(episode: ImdbEpisodeInfo) -> dict:
    """Episode-level fields: its title and year, season and episode numbers."""
    fields: dict = {"title": episode.name, "release_date": episode.air_date}
    if episode.season is not None:
        fields["season_number"] = episode.season
    if episode.episode_number:
        fields["episode_number"] = episode.episode_number
    return {k: v for k, v in fields.items() if v not in ("", None)}
