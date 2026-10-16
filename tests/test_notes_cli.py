"""The command line's `notes` command (videocli/cmd_notes.py) on folders of empty files and a fake TMDB / Wikipedia."""

import json

import pytest

from core import config
from tests.notes_helpers import fake_fetchers, hit, make_movie, make_series
from videocli import cmd_notes
from videocli.main import main


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "videoredactor_settings.ini")
    monkeypatch.setattr(cmd_notes, "make_cache", lambda args: None)


@pytest.fixture
def fetchers(monkeypatch):
    holder = {}

    def make(use_wikipedia):
        holder["f"] = fake_fetchers(wikipedia=use_wikipedia, **holder.get("extra", {}))
        return holder["f"]

    monkeypatch.setattr(cmd_notes, "make_fetchers", make)
    return holder


def run_json(capsys, *argv):
    code = main(["notes", *argv, "--json"])
    return code, json.loads(capsys.readouterr().out)


def statuses(document):
    return {r["folder"].rsplit("\\", 1)[-1].rsplit("/", 1)[-1]: r["status"] for r in document["results"]}


def test_a_report_by_default_writes_nothing(tmp_path, capsys, fetchers):
    show = make_series(tmp_path)
    movie = make_movie(tmp_path)
    code, document = run_json(capsys, str(tmp_path))
    assert code == 0 and statuses(document) == {show.name: "would-write", movie.name: "would-write"}
    assert not (show / "series.md").exists() and not (movie / "about.md").exists()
    assert document["confident"] == 2 and document["would_write"] == 2 and document["written"] == 0


def test_write_writes_the_confident_drafts_and_never_overwrites(tmp_path, capsys, fetchers):
    show = make_series(tmp_path)
    movie = make_movie(tmp_path)
    code, document = run_json(capsys, str(tmp_path), "--write")
    assert code == 0 and document["written"] == 2
    assert "title: Breaking Bad" in (show / "series.md").read_text(encoding="utf-8")
    assert (movie / "about.md").is_file()
    code, document = run_json(capsys, str(tmp_path), "--write")  # a second run: both have notes now
    assert statuses(document) == {show.name: "has-note", movie.name: "has-note"} and document["written"] == 0


def test_kind_limits_what_is_looked_at(tmp_path, capsys, fetchers):
    make_series(tmp_path)
    movie = make_movie(tmp_path)
    code, document = run_json(capsys, str(tmp_path), "--kind", "movie")
    assert statuses(document) == {movie.name: "would-write"}


def test_an_uncertain_match_is_reported_with_its_candidates_and_not_written(tmp_path, capsys, fetchers):
    show = make_series(tmp_path, name="Breaking Bad")
    fetchers["extra"] = {"tmdb_extra": [(r"/search/tv\?", {"results": [
        hit("tv", 1396, "Breaking Bad", "2008-01-20"), hit("tv", 99, "Breaking Bad", "1999-05-05")]})]}
    code, document = run_json(capsys, str(tmp_path), "--write")
    [row] = document["results"]
    assert row["status"] == "review" and not (show / "series.md").exists()
    assert {c["id"] for c in row["candidates"]} == {"1396", "99"} and document["uncertain"] == 1


def test_include_uncertain_writes_the_one_candidate_whose_seasons_fit_the_folder(tmp_path, capsys, fetchers):
    show = make_series(tmp_path, name="Breaking Bad (2008)")
    # two titles with the folder's name and year, but only the real one has the seasons that are in the folder
    fetchers["extra"] = {"tmdb_extra": [(r"/search/tv\?", {"results": [
        hit("tv", 1396, "Breaking Bad", "2008-01-20"), hit("tv", 98, "Breaking Bad", "2008-06-01")]})]}
    code, document = run_json(capsys, str(tmp_path), "--write")
    assert statuses(document) == {show.name: "review"} and not (show / "series.md").exists()
    code, document = run_json(capsys, str(tmp_path), "--write", "--include-uncertain")
    [row] = document["results"]
    assert row["status"] == "written" and row["from_uncertain"] is True and document["from_uncertain"] == 1
    assert "https://www.themoviedb.org/tv/1396" in (show / "series.md").read_text(encoding="utf-8")


def test_include_uncertain_leaves_two_equally_good_candidates_alone(tmp_path, capsys, fetchers):
    from tests.notes_helpers import BREAKING_BAD

    show = make_series(tmp_path, name="Breaking Bad (2008)")
    twin = dict(BREAKING_BAD, id=98)
    fetchers["extra"] = {"tmdb_extra": [
        (r"/tv/98\?", twin), (r"/search/tv\?", {"results": [hit("tv", 1396, "Breaking Bad", "2008-01-20"),
                                                            hit("tv", 98, "Breaking Bad", "2008-06-01")]})]}
    code, document = run_json(capsys, str(tmp_path), "--write", "--include-uncertain")
    [row] = document["results"]
    assert row["status"] == "review" and "fits name and year" in row["message"] and not (show / "series.md").exists()


def test_dry_run_never_writes_even_with_write(tmp_path, capsys, fetchers):
    show = make_series(tmp_path)
    code, document = run_json(capsys, str(tmp_path), "--write", "--dry-run")
    assert statuses(document) == {show.name: "would-write"} and not (show / "series.md").exists()


def test_no_wikipedia_leaves_the_introduction_out_and_asks_wikipedia_nothing(tmp_path, capsys, fetchers):
    show = make_series(tmp_path)
    code, document = run_json(capsys, str(tmp_path), "--write", "--no-wikipedia")
    assert "CC BY-SA" not in (show / "series.md").read_text(encoding="utf-8") and fetchers["f"].wiki.urls == []


def test_a_missing_tmdb_key_is_reported_not_a_crash(tmp_path, capsys, monkeypatch):
    make_series(tmp_path)
    import core.notes_tmdb as notes_tmdb

    monkeypatch.setattr(notes_tmdb, "get_api_key", lambda: None)
    monkeypatch.setattr(cmd_notes, "make_fetchers", lambda wiki: fake_fetchers(wikipedia=wiki, key=None))
    code, document = run_json(capsys, str(tmp_path))
    assert code == 1 and document["failed"] == 1 and "No TMDB API key" in document["problems"][0]


def test_nothing_to_find_is_an_error(tmp_path, capsys):
    from redactor_common.cli import CliError

    (tmp_path / "empty").mkdir()
    with pytest.raises(CliError, match="no series or film folders"):
        main(["notes", str(tmp_path), "--json"])


def test_the_update_flag_replaces_only_older_untouched_notes(tmp_path, capsys, fetchers, monkeypatch):
    from redactor_common.core import trash
    from redactor_common.core.notes import NoteFrontMatter, render_note

    show = make_series(tmp_path)
    old = render_note(NoteFrontMatter(title="Breaking Bad", generator="videoredactor 2026-01-01#01", note_format=1), "# Breaking Bad\n\nOld.")
    (show / "series.md").write_bytes(old.encode("utf-8"))
    monkeypatch.setattr(trash, "trash_with_retries", lambda *a, **k: lambda path: __import__("os").remove(path))
    code, document = run_json(capsys, str(tmp_path), "--update")
    assert statuses(document) == {show.name: "would-update"}
    code, document = run_json(capsys, str(tmp_path), "--update", "--write")
    assert statuses(document) == {show.name: "updated"} and "Old." not in (show / "series.md").read_text(encoding="utf-8")
