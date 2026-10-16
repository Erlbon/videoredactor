"""Create Notes: which video folders get a note, and what they hold (core/notes_local.py), on real folders of empty files."""

from pathlib import Path

from core.notes_local import (
    KIND_MOVIE, KIND_SERIES, classify, count_targets, is_season_folder, read_movie, read_series, scan, strip_country_tag,
    title_and_year,
)
from tests.notes_helpers import make_movie, make_series, touch


def test_title_and_year_from_folder_names():
    assert title_and_year("Breaking Bad (2008)") == ("Breaking Bad", "2008")
    assert title_and_year("The Wire [2002]") == ("The Wire", "2002")
    assert title_and_year("Blade.Runner.1982.1080p.BluRay") == ("Blade Runner", "1982")
    assert title_and_year("Dark") == ("Dark", "")
    assert title_and_year("The Office (US)") == ("The Office (US)", "")
    assert strip_country_tag("The Office (US)") == "The Office"


def test_season_folders_are_recognised():
    assert all(is_season_folder(n) for n in ("Season 1", "season 02", "S03", "Specials", "Sesong 4", "Staffel 2"))
    assert not any(is_season_folder(n) for n in ("Seasoned", "Breaking Bad", "Sam"))


def test_a_series_folder_with_season_folders_is_one_series_target(tmp_path):
    show = make_series(tmp_path)
    found = scan([tmp_path])
    assert [(t.folder, t.kind) for t in found.targets] == [(show, KIND_SERIES)]
    assert found.targets[0].note_kind == "series"


def test_a_flat_series_folder_and_its_seasons_are_read(tmp_path):
    show = make_series(tmp_path, seasons={1: 3, 2: 2}, season_folders=False)
    series = read_series(show)
    assert (series.name, series.year) == ("Breaking Bad", "2008")
    assert {s: len(f) for s, f in series.seasons.items()} == {1: 3, 2: 2}
    assert series.episodes == {1: [1, 2, 3], 2: [1, 2]} and series.episode_count == 5


def test_a_movie_folder_is_one_movie_target_with_its_note_kind(tmp_path):
    movie = make_movie(tmp_path)
    found = scan([tmp_path])
    assert [(t.folder, t.kind, t.note_kind) for t in found.targets] == [(movie, KIND_MOVIE, "about")]
    local = read_movie(movie)
    assert (local.title, local.year, local.files) == ("The Matrix", "1999", ["The.Matrix.1999.1080p.mkv"])


def test_the_year_may_come_from_the_file_when_the_folder_has_none(tmp_path):
    movie = make_movie(tmp_path, name="Heat", file="Heat.1995.720p.mkv")
    assert read_movie(movie).year == "1995"


def test_a_folder_of_many_loose_films_is_not_a_title_and_is_walked_into(tmp_path):
    for n in range(6):
        touch(tmp_path / "Movies" / f"Film {n}.2001.mkv")
    make_movie(tmp_path / "Movies", "Alien (1979)", "Alien.1979.mkv")
    found = scan([tmp_path])
    assert [t.folder.name for t in found.targets] == ["Alien (1979)"]
    assert classify(tmp_path / "Movies") == ""


def test_generic_library_folders_are_never_titles(tmp_path):
    touch(tmp_path / "Movies" / "Only One.2010.mkv")
    assert classify(tmp_path / "Movies") == ""


def test_a_chosen_season_folder_stands_for_its_series(tmp_path):
    show = make_series(tmp_path)
    found = scan([show / "Season 2"])
    assert [t.folder for t in found.targets] == [show]


def test_a_chosen_file_stands_for_its_folder(tmp_path):
    movie = make_movie(tmp_path)
    found = scan([movie / "The.Matrix.1999.1080p.mkv"])
    assert [t.folder for t in found.targets] == [movie]


def test_a_folder_that_already_has_a_note_is_left_alone_in_any_case(tmp_path):
    show = make_series(tmp_path)
    (show / "ABOUT.MD").write_text("mine\n", encoding="utf-8")
    found = scan([tmp_path])
    assert found.targets == [] and "already has ABOUT.MD" in found.skipped[0].reason


def test_include_subfolders_off_looks_only_at_the_named_folder(tmp_path):
    make_series(tmp_path)
    assert scan([tmp_path], include_subfolders=False).targets == []
    show = tmp_path / "Breaking Bad (2008)"
    assert [t.folder for t in scan([show], include_subfolders=False).targets] == [show]


def test_kinds_limit_what_is_found(tmp_path):
    make_series(tmp_path)
    make_movie(tmp_path)
    assert {t.kind for t in scan([tmp_path]).targets} == {KIND_SERIES, KIND_MOVIE}
    assert {t.kind for t in scan([tmp_path], kinds=[KIND_MOVIE]).targets} == {KIND_MOVIE}


def test_a_missing_path_is_reported_not_an_error(tmp_path):
    found = scan([tmp_path / "nope"])
    assert found.targets == [] and found.missing == [str(tmp_path / "nope")]


def test_count_targets_counts_series_and_movies(tmp_path):
    make_series(tmp_path)
    make_movie(tmp_path)
    make_movie(tmp_path, "Alien (1979)", "Alien.1979.mkv")
    assert count_targets(str(tmp_path)) == (1, 2)
    assert count_targets(str(tmp_path / "missing")) == (0, 0)


def test_app_temp_files_do_not_count_as_videos(tmp_path):
    touch(tmp_path / "Film (2001)" / ".Film.abcd1234.redact-part.mkv")
    assert scan([tmp_path]).targets == []
