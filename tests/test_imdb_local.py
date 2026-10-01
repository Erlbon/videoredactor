"""core/imdb_local.py: queries over a database built from synthetic IMDb-style files."""

import sqlite3

import pytest
from redactor_common.core.local_db import forget_cached

from core import imdb_import, imdb_local
from core.imdb_import import BuildOptions
from tests.imdb_fixture import AKAS, BASICS, EPISODES, N, RATINGS, build

OFFICE = [
    ("tt0290978", "tvSeries", "The Office", "The Office", "0", "2001", "2003", "30", "Comedy"),
    ("tt0386676", "tvSeries", "The Office", "The Office", "0", "2005", "2013", "22", "Comedy,Sci-Fi,Adult"),
    ("tt0430951", "tvEpisode", "Halloween", "Halloween", "0", "2005", N, "22", "Comedy"),
    ("tt0664536", "tvEpisode", "Pilot", "Pilot", "0", "2005", N, "23", "Comedy"),
    ("tt0000010", "movie", "Dune: A Documentary", "Dune: A Documentary", "0", "2020", N, "60", "Documentary"),
]
OFFICE_RATINGS = [("tt0290978", "8.5", "200000"), ("tt0386676", "9.0", "700000"), ("tt0430951", "8.7", "5000"),
                  ("tt0664536", "8.4", "6000"), ("tt0000010", "6.0", "30")]
OFFICE_EPISODES = [("tt0430951", "tt0386676", "2", "5"), ("tt0664536", "tt0386676", "1", "1")]


@pytest.fixture
def db(tmp_path):
    path, _s, _f = build(
        tmp_path, basics=BASICS + OFFICE, ratings=RATINGS + OFFICE_RATINGS, episodes=EPISODES + OFFICE_EPISODES,
        akas=AKAS,
    )
    database = imdb_local.open_database(path)
    yield database
    forget_cached(path)


# --- ids ------------------------------------------------------------------------------------


def test_exact_id_lookup(db):
    found = imdb_local.title_by_id(db, "tt0087182")
    assert isinstance(found, imdb_local.ImdbMovieCandidate)
    assert (found.title, found.year, found.runtime, found.imdb_id, found.tmdb_id) == ("Dune", "1984", 137, "tt0087182", 0)
    assert found.exact and found.rating == 6.3 and found.votes == 150000
    assert imdb_local.title_by_id(db, "87182").imdb_id == "tt0087182"       # digits only
    assert imdb_local.title_by_id(db, "tt0000000") is None                  # not in the database
    assert imdb_local.title_by_id(db, "nonsense") is None
    series = imdb_local.title_by_id(db, "tt0903747")
    assert isinstance(series, imdb_local.ImdbTVCandidate) and series.name == "Breaking Bad" and series.end_year == 2013
    episode = imdb_local.title_by_id(db, "tt0959621")
    assert isinstance(episode, imdb_local.ImdbEpisodeInfo)
    assert (episode.name, episode.season, episode.episode_number, episode.series) == ("Pilot", 1, 1, 903747)


def test_find_imdb_id_in_filenames_and_comments():
    assert imdb_local.find_imdb_id("Dune (1984) {imdb-tt0087182}") == "tt0087182"
    assert imdb_local.find_imdb_id("x", "https://www.imdb.com/title/tt1160419/") == "tt1160419"
    assert imdb_local.find_imdb_id("matt0087182x", "tt12345") == ""   # not a whole id / too short
    assert imdb_local.find_imdb_id(None, "") == ""
    assert imdb_local.find_imdb_id("tt12345678") == "tt12345678"


# --- films ----------------------------------------------------------------------------------


def test_title_and_year_disambiguate_dune(db):
    old = imdb_local.search_movies(db, "Dune", "1984")
    assert (old[0].title, old[0].year, old[0].exact) == ("Dune", "1984", True)
    new = imdb_local.search_movies(db, "Dune", "2021")
    assert (new[0].year, new[0].exact) == ("2021", True)
    assert {c.year for c in old[:2]} == {"1984", "2021"}  # both are candidates either way
    assert [c.title for c in old if c.exact] == ["Dune", "Dune"]


def test_without_a_year_the_most_voted_exact_title_comes_first(db):
    got = imdb_local.search_movies(db, "Dune")
    assert got[0].year == "2021" and got[1].year == "1984"
    assert all(c.exact for c in got[:2]) and not got[2].exact  # "Dune: A Documentary" is a longer title


def test_a_year_one_off_still_ranks_right_after_the_exact_year(db):
    got = imdb_local.search_movies(db, "Dune", "1985")
    assert got[0].year == "1984"  # gap 1
    got = imdb_local.search_movies(db, "Dune", "2020")
    assert got[0].year == "2021"


def test_localized_and_original_titles_find_the_film(db):
    for query in ("Il nome della rosa", "il nome della rosa!", "Le nom de la rose", "Der Name der Rose"):
        found = imdb_local.search_movies(db, query)[0]
        assert found.title == "The Name of the Rose" and found.exact, query
    assert imdb_local.search_movies(db, "Il nome della rosa")[0].alias == "Il nome della rosa"
    assert imdb_local.search_movies(db, "The Name of the Rose")[0].alias == ""
    assert imdb_local.search_movies(db, "Dune - Der Wuestenplanet")[0].year == "1984"


def test_punctuation_ampersands_and_accents_are_folded(db):
    assert imdb_local.search_movies(db, "Law and Order The Movie")[0].exact
    assert imdb_local.search_movies(db, "law & order: the movie")[0].title == "Law & Order: The Movie"
    assert imdb_local.search_movies(db, "Amélie")[0].title == "Amelie"
    assert imdb_local.search_movies(db, "Le fabuleux destin d'Amelie Poulain")[0].title == "Amelie"


def test_a_prefix_is_the_fallback_and_never_counts_as_exact(db):
    got = imdb_local.search_movies(db, "Dun")
    assert got and not any(c.exact for c in got)
    assert imdb_local.search_movies(db, "Zzyzx") == []


def test_films_and_series_are_kept_apart(db):
    assert imdb_local.search_movies(db, "Breaking Bad") == []
    assert imdb_local.search_series(db, "Dune") == []
    assert imdb_local.search_series(db, "Breaking Bad")[0].name == "Breaking Bad"
    assert imdb_local.search_movies(db, "Pilot") == []  # episodes are not searchable by title


def test_a_title_is_required(db):
    with pytest.raises(imdb_local.ImdbLocalError):
        imdb_local.search_movies(db, "   ")


# --- series and episodes ------------------------------------------------------------------


def test_series_with_the_same_title_are_told_apart_by_year(db):
    us = imdb_local.search_series(db, "The Office", "2005")
    assert (us[0].year, us[0].exact) == ("2005", True) and us[1].year == "2001"
    uk = imdb_local.search_series(db, "The Office", "2001")
    assert uk[0].year == "2001"
    assert imdb_local.search_series(db, "The Office")[0].year == "2005"  # most votes


def test_episode_resolution(db):
    series = imdb_local.search_series(db, "The Office", "2005")[0]
    episode = imdb_local.find_episode(db, series.tconst, 2, 5)
    assert (episode.name, episode.year, episode.runtime, episode.imdb_id) == ("Halloween", 2005, 22, "tt0430951")
    assert imdb_local.find_episode(db, series.tconst, 9, 99) is None
    assert imdb_local.seasons_of(db, series.tconst) == [(1, 1), (2, 1)]
    assert [e.name for e in imdb_local.episodes_of(db, series.tconst, 1)] == ["Pilot"]
    bb = imdb_local.search_series(db, "Breaking Bad")[0]
    assert [e.episode_number for e in imdb_local.episodes_of(db, bb.tconst, 1)] == [1, 2]


# --- mapping --------------------------------------------------------------------------------


def test_field_mapping(db):
    dune = imdb_local.search_movies(db, "Dune", "1984")[0]
    assert imdb_local.movie_fields(dune) == {"title": "Dune", "release_date": "1984",
                                             "genre_tags": "Adventure, Drama, Science Fiction"}
    office = imdb_local.search_series(db, "The Office", "2005")[0]
    assert imdb_local.show_fields(office) == {"show_title": "The Office", "release_date": "2005",
                                              "genre_tags": "Comedy, Science Fiction"}  # "Adult" dropped
    episode = imdb_local.find_episode(db, office.tconst, 2, 5)
    assert imdb_local.episode_fields(episode) == {"title": "Halloween", "release_date": "2005",
                                                  "season_number": 2, "episode_number": 5}
    assert imdb_local.map_genres("Talk-Show,Reality-TV,Game-Show,Film-Noir,Action") == \
        "Talk, Reality, Game Show, Film-Noir, Action"


def test_candidates_have_the_shape_the_tmdb_dialog_uses(db):
    from core.tmdb_client import EpisodeInfo, MovieCandidate, TVCandidate

    movie = imdb_local.search_movies(db, "Dune", "1984")[0]
    assert isinstance(movie, MovieCandidate) and movie.release_date == "1984" and movie.poster_path is None
    assert "tt0087182" in movie.overview and "137 min" in movie.overview and "6.3" in movie.overview
    assert isinstance(imdb_local.search_series(db, "Breaking Bad")[0], TVCandidate)
    assert isinstance(imdb_local.find_episode(db, 903747, 1, 1), EpisodeInfo)


# --- a database built with fewer parts --------------------------------------------------------


def test_a_database_without_episodes_or_akas_still_answers(tmp_path):
    path, _s, _f = build(tmp_path, BuildOptions(include_episodes=False, include_akas=False))
    plain = imdb_local.open_database(path)
    try:
        assert imdb_local.search_movies(plain, "Dune", "1984")[0].year == "1984"
        assert imdb_local.search_movies(plain, "Il nome della rosa") == []          # no aliases stored
        assert imdb_local.find_episode(plain, 903747, 1, 1) is None
        assert imdb_local.seasons_of(plain, 903747) == []
    finally:
        plain.close()


def test_an_old_build_without_a_fulltext_index_says_so(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("create table titles (tconst integer primary key, kind, title, original_title, year, end_year, "
                "runtime, genres, is_adult, rating, votes, norm)")
    con.commit()
    con.close()
    old = imdb_local.open_database(str(path))
    try:
        with pytest.raises(imdb_local.ImdbLocalError, match="full-text"):
            imdb_local.search_movies(old, "Dune")
    finally:
        old.close()


def test_missing_or_foreign_files_are_one_clear_error(tmp_path):
    assert "no local IMDb database" in imdb_local.database_ready("")
    assert "not found" in imdb_local.database_ready(str(tmp_path / "gone.db")).lower()
    other = tmp_path / "other.db"
    sqlite3.connect(other).execute("create table x (y)").connection.close()
    assert "doesn't look like" in imdb_local.database_ready(str(other))
    with pytest.raises(imdb_local.ImdbLocalError):
        imdb_local.open_database(str(tmp_path / "gone.db"))


def test_every_query_is_local_no_network_modules_used():
    source = open(imdb_local.__file__, encoding="utf-8").read()
    for word in ("urllib", "requests", "http.client", "urlopen", "socket", "fetch_json", "_get_json"):
        assert word not in source
    assert imdb_import.SOURCE_NAME and imdb_local.SERVICE_NAME == "IMDb (local database)"
