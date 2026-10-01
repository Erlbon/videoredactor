"""core/imdb_import.py on small synthetic gz fixtures (no real IMDb data)."""

import os
import sqlite3

import pytest
from redactor_common.core.dump_import import DumpImportError, ImportCancelled

from core import imdb_import
from core.imdb_import import BuildOptions
from tests.imdb_fixture import BASICS, BASICS_HEADER, N, build, make_dataset, write_tsv


def rows(path, sql, params=()):
    con = sqlite3.connect(path)
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def numbers(path, table="titles"):
    return {r[0] for r in rows(path, f"select tconst from {table}")}


def test_default_build_keeps_the_right_titles(tmp_path):
    dest, summary, _files = build(tmp_path)
    # kept: both Dunes, the Rose, Breaking Bad, the special, Amelie, Law & Order.
    # dropped: short and game (type), adult feature, Obscure Film (2 votes), Unknown Show (1 vote).
    assert numbers(dest) == {87182, 1160419, 91605, 903747, 3333331, 118115, 172495}
    assert summary.titles == 7
    assert summary.skipped_votes == 2 and summary.skipped_adult == 1
    assert summary.titles_seen == len(BASICS)


def test_ratings_are_joined_and_missing_values_are_null(tmp_path):
    dest, _s, _f = build(tmp_path)
    kind, year, end_year, runtime, genres, rating, votes = rows(
        dest, "select kind, year, end_year, runtime, genres, rating, votes from titles where tconst = 903747")[0]
    assert (kind, year, end_year, runtime, genres, rating, votes) == (
        "tvSeries", 2008, 2013, 49, "Crime,Drama,Thriller", 9.5, 2000000)
    year, end_year = rows(dest, "select year, end_year from titles where tconst = 87182")[0]
    assert year == 1984 and end_year is None  # backslash-N became NULL, not text
    assert rows(dest, "select original_title from titles where tconst = 87182")[0][0] == ""  # same as title: not repeated
    assert rows(dest, "select original_title from titles where tconst = 91605")[0][0] == "Der Name der Rose"


def test_episodes_joined_only_for_kept_series(tmp_path):
    dest, summary, _f = build(tmp_path)
    assert rows(dest, "select tconst, parent, season, episode, title, year, runtime, rating, votes "
                      "from episodes order by episode") == [
        (959621, 903747, 1, 1, "Pilot", 2008, 58, 9.0, 30000),
        (1054724, 903747, 1, 2, "Cat's in the Bag...", 2008, 48, 8.6, 28000),
    ]  # Lost Episode (series below the vote minimum) and the orphan are gone
    assert summary.episodes == 2


def test_no_episodes_option(tmp_path):
    dest, _s, _f = build(tmp_path, BuildOptions(include_episodes=False))
    assert rows(dest, "select count(*) from episodes")[0][0] == 0
    assert 903747 in numbers(dest)


def test_episodes_need_the_episode_file_but_not_votes(tmp_path):
    files = make_dataset(tmp_path / "d")
    dest = str(tmp_path / "x.db")
    imdb_import.build_imdb_database(files["basics"], dest, files["ratings"], "", "", BuildOptions())
    assert rows(dest, "select count(*) from episodes")[0][0] == 0  # no title.episode file: no links


def test_type_choice_and_adult_and_votes_filters(tmp_path):
    options = BuildOptions(types=("movie", "short"), skip_adult=False, min_votes=0)
    dest, _s, _f = build(tmp_path, options)
    assert numbers(dest) == {87182, 1160419, 91605, 2, 9999990, 8888881, 118115, 172495}
    assert rows(dest, "select is_adult from titles where tconst = 9999990") == [(1,)]
    assert rows(dest, "select count(*) from episodes")[0][0] == 0  # tvEpisode type not chosen


def test_min_votes_threshold_is_a_spin_value(tmp_path):
    dest, _s, _f = build(tmp_path, BuildOptions(min_votes=1000))
    assert 3333331 not in numbers(dest)  # 50 votes
    assert 172495 in numbers(dest)       # 1500 votes


def test_min_votes_needs_the_ratings_file(tmp_path):
    files = make_dataset(tmp_path / "d")
    with pytest.raises(DumpImportError, match="ratings"):
        imdb_import.build_imdb_database(files["basics"], str(tmp_path / "x.db"), options=BuildOptions())
    # with 0 it works without ratings, and titles then have no rating
    dest = str(tmp_path / "y.db")
    imdb_import.build_imdb_database(files["basics"], dest, options=BuildOptions(min_votes=0, include_akas=False))
    assert rows(dest, "select rating, votes from titles where tconst = 87182") == [(None, None)]


def test_akas_region_filter_and_duplicates(tmp_path):
    dest, summary, _f = build(tmp_path)
    got = {(r[0], r[1], r[2]) for r in rows(dest, "select tconst, title, region from akas")}
    assert got == {
        (91605, "Il nome della rosa", "IT"), (91605, "Le nom de la rose", "FR"),
        (87182, "Dune - Der Wuestenplanet", "DE"), (118115, "Die fabelhafte Welt der Amelie", "DE"),
        (903747, "Breaking Bad - Reine Chemie", "DE"),
    }  # not: SE/JP regions, titles equal to the primary/original title, titles that weren't kept
    assert summary.akas == 5


def test_akas_regions_option_and_off(tmp_path):
    dest, _s, _f = build(tmp_path, BuildOptions(regions=("IT",)))
    assert {r[0] for r in rows(dest, "select title from akas")} == {"Il nome della rosa"}
    dest2 = str(tmp_path / "noakas.db")
    files = make_dataset(tmp_path / "dump")
    imdb_import.build_imdb_database(files["basics"], dest2, files["ratings"], files["episodes"], files["akas"],
                                    BuildOptions(include_akas=False))
    assert rows(dest2, "select count(*) from akas")[0][0] == 0


def test_original_title_entries_are_kept_when_they_differ(tmp_path):
    akas = [("tt0091605", "1", "Name of the Rose original", N, N, "original", N, "1")]
    dest, _s, _f = build(tmp_path, akas=akas)
    assert rows(dest, "select title, region from akas") == [("Name of the Rose original", "")]
    dest2 = str(tmp_path / "b" / "x.db")
    files = make_dataset(tmp_path / "b", akas=akas)
    imdb_import.build_imdb_database(files["basics"], dest2, files["ratings"], files["episodes"], files["akas"],
                                    BuildOptions(aka_original=False))
    assert rows(dest2, "select count(*) from akas")[0][0] == 0


def test_prebuilt_fts_finds_normalized_text(tmp_path):
    dest, _s, _f = build(tmp_path)
    from redactor_common.core.local_db import fts_query
    db = imdb_import.LocalDatabase(dest, ("titles", "titles_fts"))
    try:
        assert fts_query(db, "titles_fts", "dune", prefix=False) and set(fts_query(db, "titles_fts", "dune")) == {87182, 1160419}
        assert fts_query(db, "titles_fts", "Law and Order", prefix=False) == [172495]   # "&" folded like the query side
        assert fts_query(db, "titles_fts", "der name der rose", prefix=False) == [91605]  # original title is indexed
        assert fts_query(db, "akas_fts", "nome della rosa", prefix=False, key="tconst", from_table="akas") == [91605]
        assert fts_query(db, "titles_fts", "amélie") == [118115] or fts_query(db, "titles_fts", "amelie") == [118115]
    finally:
        db.close()


def test_info_table_and_describe(tmp_path):
    dest, _s, _f = build(tmp_path)
    info = imdb_import.database_info(dest)
    assert info["recipe"] == imdb_import.RECIPE and info["rows.titles"] == "7"
    assert info["basics_file"] == "title.basics.tsv.gz" and info["akas_file"] == "title.akas.tsv.gz"
    assert int(info["size.file"]) > 0 and "fts.titles_fts" in info and "fts.akas_fts" in info
    assert BuildOptions.from_json(info["options_json"]) == BuildOptions()
    text = imdb_import.describe_database(dest)
    assert "7 titles" in text and "2 episodes" in text and "5 alternative titles" in text


def test_database_info_rejects_foreign_files(tmp_path):
    other = tmp_path / "other.db"
    con = sqlite3.connect(other)
    con.execute("create table titles (x)")
    con.commit()
    con.close()
    with pytest.raises(imdb_import.ImdbDatabaseError):
        imdb_import.database_info(str(other))
    with pytest.raises(imdb_import.ImdbDatabaseError):
        imdb_import.database_info(str(tmp_path / "missing.db"))


def test_renamed_header_column_fails_loudly_and_leaves_nothing(tmp_path):
    bad_header = [h if h != "titleType" else "type" for h in BASICS_HEADER]
    files = make_dataset(tmp_path / "d")
    write_tsv(files["basics"], bad_header, BASICS)
    dest = str(tmp_path / "x.db")
    with pytest.raises(DumpImportError, match="titleType"):
        imdb_import.build_imdb_database(files["basics"], dest, files["ratings"], files["episodes"], files["akas"])
    assert not os.path.exists(dest) and not os.path.exists(dest + ".partial")
    assert not os.path.exists(dest + ".imdb.tmp")


def test_wrong_file_in_the_ratings_slot_fails_loudly(tmp_path):
    files = make_dataset(tmp_path / "d")
    with pytest.raises(DumpImportError, match="numVotes|averageRating"):
        imdb_import.build_imdb_database(files["basics"], str(tmp_path / "x.db"), files["basics"])


def test_extra_new_column_and_crlf_and_bom_are_tolerated(tmp_path):
    files = make_dataset(tmp_path / "d")
    write_tsv(files["basics"], BASICS_HEADER + ["newColumn"], [r + ("x",) for r in BASICS], newline="\r\n", bom=True)
    dest = str(tmp_path / "x.db")
    imdb_import.build_imdb_database(files["basics"], dest, files["ratings"], files["episodes"], files["akas"])
    assert 903747 in numbers(dest)


def test_nothing_kept_is_an_error(tmp_path):
    files = make_dataset(tmp_path / "d")
    with pytest.raises(DumpImportError, match="No titles were kept"):
        imdb_import.build_imdb_database(files["basics"], str(tmp_path / "x.db"), files["ratings"],
                                        options=BuildOptions(types=("tvShort",), min_votes=0))
    assert not os.path.exists(str(tmp_path / "x.db"))


def test_missing_files_and_no_types(tmp_path):
    files = make_dataset(tmp_path / "d")
    with pytest.raises(DumpImportError, match="not found"):
        imdb_import.build_imdb_database(str(tmp_path / "nope.gz"), str(tmp_path / "x.db"))
    with pytest.raises(DumpImportError, match="not found"):
        imdb_import.build_imdb_database(files["basics"], str(tmp_path / "x.db"), str(tmp_path / "nope.gz"))
    with pytest.raises(DumpImportError, match="types"):
        imdb_import.build_imdb_database(files["basics"], str(tmp_path / "x.db"), options=BuildOptions(types=()))


def test_cancel_removes_the_partial_file_and_keeps_an_old_database(tmp_path):
    dest, _s, files = build(tmp_path)
    before = os.path.getsize(dest)
    with pytest.raises(ImportCancelled):
        imdb_import.build_imdb_database(files["basics"], dest, files["ratings"], files["episodes"], files["akas"],
                                        cancelled=lambda: True)
    assert os.path.getsize(dest) == before  # the previous build is still there
    assert not os.path.exists(dest + ".partial") and not os.path.exists(dest + ".imdb.tmp")
    # a cancel in the middle of a fresh build leaves nothing under the real name either
    fresh = str(tmp_path / "fresh.db")
    calls = {"n": 0}

    def cancel_later() -> bool:
        calls["n"] += 1
        return calls["n"] > 1

    big = [(f"tt{n:07d}", "movie", f"Film {n}", f"Film {n}", "0", "2000", N, "90", "Drama") for n in range(1, 2500)]
    files = make_dataset(tmp_path / "big", basics=big, ratings=[], episodes=[], akas=[])
    with pytest.raises(ImportCancelled):
        imdb_import.build_imdb_database(files["basics"], fresh, options=BuildOptions(min_votes=0),
                                        cancelled=cancel_later)
    assert not os.path.exists(fresh) and not os.path.exists(fresh + ".partial")


def test_progress_reaches_one_and_never_goes_backwards(tmp_path):
    files = make_dataset(tmp_path / "d")
    seen = []
    imdb_import.build_imdb_database(files["basics"], str(tmp_path / "x.db"), files["ratings"], files["episodes"],
                                    files["akas"], progress=seen.append)
    assert seen[-1] == 1.0 and all(0.0 <= v <= 1.0 for v in seen)


def test_options_json_round_trip_and_garbage():
    options = BuildOptions(types=("movie",), skip_adult=False, min_votes=20, include_episodes=False,
                           include_akas=False, regions=("NO", "it"), aka_original=False)
    again = BuildOptions.from_json(options.to_json())
    assert again.types == ("movie",) and again.min_votes == 20 and again.regions == ("NO", "IT")
    assert not again.skip_adult and not again.include_episodes and not again.include_akas and not again.aka_original
    for garbage in ("", "not json", "[]", '{"types": "movie", "min_votes": -5, "skip_adult": "yes"}'):
        assert BuildOptions.from_json(garbage) == BuildOptions()


def test_licence_notice_text():
    assert "personal, non-commercial" in imdb_import.LICENCE_NOTICE
    assert "republished, resold or repurposed" in imdb_import.LICENCE_NOTICE
    assert imdb_import.CONDITIONS_URL.startswith("https://www.imdb.com")


def test_tconst_helpers():
    assert imdb_import.tconst_number("tt0133093") == 133093
    assert imdb_import.tconst_number("nm0000001") is None and imdb_import.tconst_number("tt") is None
    assert imdb_import.imdb_id(133093) == "tt0133093" and imdb_import.imdb_id(12345678) == "tt12345678"
