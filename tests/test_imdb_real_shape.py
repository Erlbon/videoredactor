"""The IMDb importer and lookup on generated data with the real SHAPE of IMDb's files (null patterns, id
lengths, type and region mixes) -- invented ids and titles only; no IMDb data is, or may be, committed."""

import random
import sqlite3

import pytest
from redactor_common.core.local_db import forget_cached, normalize_words

from core import imdb_import, imdb_local
from core.imdb_import import BuildOptions
from tests import imdb_shape
from tests.imdb_fixture import BASICS_HEADER, N, write_tsv

N_TITLES = 2500


@pytest.fixture(scope="module")
def shaped(tmp_path_factory):
    folder = tmp_path_factory.mktemp("shaped")
    return imdb_shape.generate(str(folder), N_TITLES)


def build(tmp_path, shaped, options=None):
    dest = str(tmp_path / "shaped.db")
    summary = imdb_import.build_imdb_database(
        shaped["basics"], dest, shaped["ratings"], shaped["episodes"], shaped["akas"], options)
    return dest, summary


def query(path, sql, params=()):
    con = sqlite3.connect(path)
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def number(tconst):
    return int(tconst[2:])


def expected_titles(shaped, options):
    votes = {number(r[0]): int(r[2]) for r in shaped["rows"]["ratings"]}
    out = {}
    for tconst, kind, title, original, adult, *_rest in shaped["rows"]["basics"]:
        if kind not in options.types or kind == "tvEpisode":
            continue
        if options.skip_adult and adult == "1":
            continue
        if options.min_votes and votes.get(number(tconst), 0) < options.min_votes:
            continue
        out[number(tconst)] = (title, original)
    return out


def test_the_shaped_data_really_has_the_null_patterns(shaped):
    rows = shaped["rows"]
    assert any(r[5] == N for r in rows["basics"])                 # a null startYear
    assert sum(r[7] == N for r in rows["basics"]) > N_TITLES * 0.1  # null runtime on many
    assert sum(r[8] == N for r in rows["basics"]) > 50              # null genres
    assert any(r[2] == N and r[3] == N for r in rows["episodes"])   # episodes with no season and no number
    assert any(len(r[1]) == 10 for r in rows["episodes"])           # a 10-character parent id
    assert {r[6] for r in rows["basics"]} >= {N}
    kinds = {r[1] for r in rows["basics"]}
    assert {"movie", "short", "tvSeries", "tvEpisode", "tvMovie", "tvMiniSeries", "videoGame"} <= kinds
    assert any(r[3] == N and r[7] == "1" for r in rows["akas"])    # an original title with no region
    assert {r[5] for r in rows["akas"]} >= {N, "imdbDisplay", "original", "working"}


def test_default_build_matches_an_independent_computation(tmp_path, shaped):
    dest, summary = build(tmp_path, shaped)
    options = BuildOptions()
    want = expected_titles(shaped, options)
    assert {r[0] for r in query(dest, "select tconst from titles")} == set(want)
    assert summary.bad_lines == 0 and summary.titles_seen == N_TITLES
    # null year / runtime / genres end up as NULL / NULL / "" and never as text
    truth = {number(t[0]): t for t in shaped["truth"]}
    for tconst, year, runtime, genres, end_year in query(dest, "select tconst, year, runtime, genres, end_year from titles"):
        row = next(r for r in shaped["rows"]["basics"] if number(r[0]) == tconst) if tconst % 97 == 0 else None
        assert year == truth[tconst][3]
        if row:
            assert (runtime is None) == (row[7] == N) and (genres == "") == (row[8] == N)
            assert (end_year is None) == (row[6] == N)
    assert not query(dest, "select 1 from titles where year = '\\N' or genres = '\\N' or title = '\\N' limit 1")
    forget_cached(dest)


def test_votes_ratings_and_adult_are_applied(tmp_path, shaped):
    dest, _s = build(tmp_path, shaped)
    assert not query(dest, "select 1 from titles where is_adult = 1 limit 1")
    assert not query(dest, "select 1 from titles where votes < 5 or votes is null limit 1")
    ratings = {number(r[0]): (float(r[1]), int(r[2])) for r in shaped["rows"]["ratings"]}
    for tconst, rating, votes in query(dest, "select tconst, rating, votes from titles"):
        assert (rating, votes) == ratings[tconst]


def test_episodes_only_for_kept_series_and_only_with_numbers(tmp_path, shaped):
    dest, summary = build(tmp_path, shaped)
    kept = set(expected_titles(shaped, BuildOptions()))
    want = {}
    for tconst, parent, season, episode in shaped["rows"]["episodes"]:
        if season == N or episode == N or number(parent) not in kept:
            continue
        want[number(tconst)] = (number(parent), int(season), int(episode))
    basics = {number(r[0]): r for r in shaped["rows"]["basics"]}
    want = {t: v for t, v in want.items() if t in basics and basics[t][1] == "tvEpisode" and basics[t][4] != "1"}
    got = {r[0]: (r[1], r[2], r[3]) for r in query(dest, "select tconst, parent, season, episode from episodes")}
    assert got == want and summary.episodes == len(want)
    assert any(r[2] == N for r in shaped["rows"]["episodes"])  # the null-numbered ones really were in the input
    assert not query(dest, "select 1 from episodes where season is null or episode is null limit 1")
    assert number("tt91999999") not in got  # parent missing from the basics file


def test_akas_follow_the_region_rule_and_skip_the_titles_own_names(tmp_path, shaped):
    dest, summary = build(tmp_path, shaped)
    options = BuildOptions()
    titles = expected_titles(shaped, options)
    want = set()
    for tconst, _ordering, title, region, language, _types, _attrs, original in shaped["rows"]["akas"]:
        n = number(tconst)
        if n not in titles:
            continue
        if region.upper() not in options.regions and not (original == "1" and options.aka_original):
            continue
        norm = normalize_words(" ".join(title.split()))
        primary, orig = titles[n]
        if not norm or norm in (normalize_words(" ".join(primary.split())), normalize_words(" ".join(orig.split()))):
            continue
        want.add((n, norm))
    got = {(r[0], r[1]) for r in query(dest, "select tconst, norm from akas")}
    assert got == want and summary.akas == len(want)
    regions = {r[0] for r in query(dest, "select distinct region from akas")}
    assert regions <= set(options.regions) | {""}


def test_include_everything_keeps_every_non_episode_title(tmp_path, shaped):
    options = BuildOptions(types=tuple(t for t, _l, _d in imdb_import.TYPE_CHOICES), skip_adult=False, min_votes=0,
                           regions=tuple(c for c, _l in imdb_import.REGION_CHOICES))
    dest, summary = build(tmp_path, shaped, options)
    assert {r[0] for r in query(dest, "select tconst from titles")} == set(expected_titles(shaped, options))
    assert summary.bad_lines == 0 and summary.skipped_votes == 0 and summary.skipped_adult == 0
    assert summary.titles == sum(1 for r in shaped["rows"]["basics"] if r[1] != "tvEpisode")


def test_every_kept_title_is_found_again_by_its_own_title_and_year(tmp_path, shaped):
    dest, _s = build(tmp_path, shaped)
    db = imdb_local.open_database(dest)
    try:
        rng = random.Random(3)
        kept = [t for t in shaped["truth"] if number(t[0]) in expected_titles(shaped, BuildOptions())]
        for tconst, kind, title, year, _votes, _adult in rng.sample(kept, 150):
            search = imdb_local.search_series if kind in imdb_local.SERIES_KINDS else imdb_local.search_movies
            found = search(db, title, str(year) if year else None, limit=40)
            hit = [c for c in found if c.tconst == number(tconst)]
            assert hit and hit[0].exact, (tconst, title)
    finally:
        db.close()
        forget_cached(dest)


def test_kept_alternative_titles_find_their_film(tmp_path, shaped):
    dest, _s = build(tmp_path, shaped)
    db = imdb_local.open_database(dest)
    try:
        rows = query(dest, "select a.tconst, a.title from akas a join titles t using (tconst) "
                           "where t.kind in ('movie','short','tvMovie','video') limit 120")
        assert rows
        for tconst, alias in rows:
            found = imdb_local.search_movies(db, alias, limit=60)
            hit = [c for c in found if c.tconst == tconst]
            assert hit and hit[0].exact, (tconst, alias)
    finally:
        db.close()
        forget_cached(dest)


def test_a_database_from_the_shaped_files_passes_sqlite_integrity(tmp_path, shaped):
    dest, _s = build(tmp_path, shaped)
    assert query(dest, "pragma integrity_check") == [("ok",)]
    info = imdb_import.database_info(dest)
    assert info["rows.titles"] == str(len(query(dest, "select tconst from titles")))
    forget_cached(dest)


def test_stray_whitespace_and_quotes_in_titles(tmp_path):
    rows = [
        ("tt90100001", "movie", '  A  "Quoted"   Title ', "A  Quoted Title", "0", "2001", N, "90", "Drama"),
        ("tt90100002", "movie", "Tab Nbsp", "Tab Nbsp", "0", "2002", N, N, N),
    ]
    files = {"basics": write_tsv(tmp_path / "b.tsv.gz", BASICS_HEADER, rows)}
    dest = str(tmp_path / "w.db")
    imdb_import.build_imdb_database(files["basics"], dest, options=BuildOptions(min_votes=0, include_akas=False))
    assert query(dest, "select title, original_title from titles where tconst = 90100001") == [('A "Quoted" Title', "A Quoted Title")]
    db = imdb_local.open_database(dest)
    try:
        assert imdb_local.search_movies(db, 'a quoted title')[0].tconst == 90100001
    finally:
        db.close()
        forget_cached(dest)
