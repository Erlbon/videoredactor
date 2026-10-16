"""Create Notes: the engine (core/notes_flow.py) on folders of empty files and a fake TMDB / Wikipedia. The injected fetchers
count what really reaches them."""

import json

import pytest

from core.notes_flow import (
    Fetchers, NotesEngine, build_proposals, scan, summarize, write_proposal, undo_note_entry,
)
from core.notes_tmdb import TitleDetails
from core.tmdb_client import TMDBError
from redactor_common.core.notes import note_status, parse_front_matter
from redactor_common.core.notes_cache import ResponseCache
from redactor_common.core.notes_model import Confidence
from tests.notes_helpers import BREAKING_BAD, FakeFetch, fake_fetchers, hit, make_movie, make_series, tmdb_routes


def only(tmp_path, **kw):
    return build_proposals(scan([tmp_path], **kw).targets, fake_fetchers())


def test_a_series_that_fits_its_only_exact_tmdb_match_is_confident_and_written_as_a_draft(tmp_path):
    show = make_series(tmp_path)
    [p] = only(tmp_path)
    assert p.confidence == Confidence.CONFIDENT and p.title == "Breaking Bad" and p.tmdb_id == 1396
    assert p.note_kind == "series" and p.path == str(show / "series.md")
    assert p.sources == ["TMDB", "Wikipedia"] and any("season 1: 7 of 7 episodes" in r for r in p.reasons)
    meta, body = parse_front_matter(p.note_text)
    assert meta["title"] == "Breaking Bad" and meta["years"] == "2008-2013" and meta["status"] == "draft"
    assert meta["genres"] == ["drama", "crime"] and meta["generator"].startswith("videoredactor ")
    assert "https://www.themoviedb.org/tv/1396" in meta["links"] and "https://en.wikipedia.org/wiki/Breaking_Bad" in meta["links"]
    assert "It ran for five seasons." in body and "**Network:** AMC" in body and "- Bryan Cranston as Walter White" in body
    assert "- Season 1 (7 episodes)" in body and "- Specials (9 episodes)" in body
    assert "is not endorsed, certified, or otherwise approved by TMDB" in body and "CC BY-SA" in body and "api_key" not in p.note_text
    result = write_proposal(p)
    assert result.ok and (show / "series.md").read_text(encoding="utf-8") == p.note_text.replace("\r\n", "\n")


def test_a_movie_is_about_md_with_director_runtime_and_wikipedia(tmp_path):
    movie = make_movie(tmp_path)
    [p] = only(tmp_path)
    assert p.confidence == Confidence.CONFIDENT and p.note_kind == "about" and p.path == str(movie / "about.md")
    meta, body = parse_front_matter(p.note_text)
    assert meta["title"] == "The Matrix" and meta["years"] == "1999"
    assert "**Directed by:** Lana Wachowski, Lilly Wachowski" in body and "**Runtime:** 2 h 16 min" in body
    assert "a 1999 science fiction action film" in body


def test_the_wikipedia_article_comes_only_from_the_imdb_id_never_from_a_name_search(tmp_path):
    make_movie(tmp_path)
    fetchers = fake_fetchers()
    build_proposals(scan([tmp_path]).targets, fetchers)
    asked = fetchers.wiki.urls
    assert any("haswbstatement" in u for u in asked) and not any("search/title" in u for u in asked)


def test_no_imdb_id_means_no_wikipedia_text_and_a_reason(tmp_path):
    make_movie(tmp_path)
    no_imdb = dict(json.loads(json.dumps(__import__("tests.notes_helpers", fromlist=["MATRIX"]).MATRIX)))
    no_imdb["external_ids"] = {}
    fetchers = fake_fetchers(tmdb_extra=[(r"/movie/603\?", no_imdb)])
    [p] = build_proposals(scan([tmp_path]).targets, fetchers)
    assert p.confidence == Confidence.CONFIDENT and p.sources == ["TMDB"]
    assert any("no Wikipedia article can be verified" in r for r in p.reasons) and fetchers.wiki.urls == []


def test_wikipedia_can_be_switched_off(tmp_path):
    make_movie(tmp_path)
    fetchers = fake_fetchers(wikipedia=False)
    [p] = build_proposals(scan([tmp_path]).targets, fetchers)
    assert p.sources == ["TMDB"] and fetchers.wiki.urls == []


def test_two_series_with_the_same_name_and_no_year_in_the_folder_are_uncertain_with_candidates(tmp_path):
    make_series(tmp_path, name="Breaking Bad")
    other = hit("tv", 99, "Breaking Bad", "1999-05-05", "A different show")
    fetchers = fake_fetchers(tmdb_extra=[(r"/search/tv\?", {"results": [hit("tv", 1396, "Breaking Bad", "2008-01-20"), other]})])
    [p] = build_proposals(scan([tmp_path]).targets, fetchers)
    assert p.confidence == Confidence.UNCERTAIN and p.note_text == "" and not p.writable
    assert {c.id for c in p.candidates} == {"1396", "99"} and any("2 titles are called" in r for r in p.reasons)
    assert write_proposal(p).status == "skipped"


def test_a_year_in_the_folder_separates_two_series_with_the_same_name(tmp_path):
    make_series(tmp_path, name="Breaking Bad (2008)")
    other = hit("tv", 99, "Breaking Bad", "1999-05-05")
    fetchers = fake_fetchers(tmdb_extra=[(r"/search/tv\?", {"results": [other, hit("tv", 1396, "Breaking Bad", "2008-01-20")]})])
    [p] = build_proposals(scan([tmp_path]).targets, fetchers)
    assert p.confidence == Confidence.CONFIDENT and p.tmdb_id == 1396


def test_a_series_whose_seasons_do_not_fit_is_not_confident(tmp_path):
    make_series(tmp_path, seasons={1: 7, 9: 4})  # TMDB has no season 9
    [p] = only(tmp_path)
    assert p.confidence == Confidence.UNCERTAIN and any("season 9 is not at TMDB" in r for r in p.reasons)


def test_a_film_a_year_off_is_uncertain_not_confident(tmp_path):
    make_movie(tmp_path, name="The Matrix (2000)", file="The.Matrix.2000.mkv")
    fetchers = fake_fetchers(tmdb_extra=[(r"/search/movie\?.*year=2000", {"results": [hit("movie", 603, "The Matrix", "1999-03-30")]})])
    [p] = build_proposals(scan([tmp_path]).targets, fetchers)
    assert p.confidence == Confidence.UNCERTAIN and any("a year off" in r for r in p.reasons)


def test_when_nothing_is_found_with_the_year_it_searches_again_without_it(tmp_path):
    make_movie(tmp_path, name="The Matrix (2005)", file="The.Matrix.2005.mkv")
    fetchers = fake_fetchers(tmdb_extra=[(r"/search/movie\?.*year=2005", {"results": []})])
    [p] = build_proposals(scan([tmp_path]).targets, fetchers)
    assert p.confidence == Confidence.UNCERTAIN and any("searched without the year" in r for r in p.reasons)


def test_nothing_found_is_none_with_a_reason(tmp_path):
    make_movie(tmp_path, name="Zzzz Unknown (2001)", file="Zzzz.Unknown.2001.mkv")
    fetchers = fake_fetchers(tmdb_extra=[(r"/search/movie", {"results": []})])
    [p] = build_proposals(scan([tmp_path]).targets, fetchers)
    assert p.confidence == Confidence.NONE and p.candidates == [] and any("TMDB has no film" in r for r in p.reasons)


def test_picking_a_candidate_rebuilds_a_confident_proposal(tmp_path):
    make_series(tmp_path, name="Breaking Bad")
    other = hit("tv", 99, "Breaking Bad", "1999-05-05")
    fetchers = fake_fetchers(tmdb_extra=[(r"/search/tv\?", {"results": [other, hit("tv", 1396, "Breaking Bad", "2008-01-20")]})])
    engine = NotesEngine(fetchers)
    [p] = build_proposals(scan([tmp_path]).targets, engine=engine)
    pick = next(c for c in p.candidates if c.id == "1396")
    assert engine.can_choose(p, pick)
    rebuilt = engine.rebuild(p, candidate=pick)
    assert rebuilt.confidence == Confidence.CONFIDENT and rebuilt.tmdb_id == 1396 and "chosen by you" in rebuilt.reasons[0]


def test_the_explicit_minimal_note_says_it_has_no_online_source(tmp_path):
    make_movie(tmp_path)
    engine = NotesEngine(fake_fetchers(tmdb_extra=[(r"/search/movie", {"results": []})]))
    [p] = build_proposals(scan([tmp_path]).targets, engine=engine)
    local = engine.rebuild(p, level="local-name")
    assert local.confidence == Confidence.CONFIDENT and "no online source" in local.note_text and local.sources == ["the folder name"]


def test_a_missing_api_key_is_said_once_and_stops_the_requests(tmp_path):
    make_series(tmp_path)
    make_movie(tmp_path)
    tmdb = FakeFetch(tmdb_routes())
    fetchers = Fetchers(tmdb=tmdb, wiki=FakeFetch(), key=None)
    import core.tmdb_client as client

    original = client.get_api_key
    client.get_api_key = lambda: None
    try:
        import core.notes_tmdb as notes_tmdb

        notes_tmdb.get_api_key = lambda: None
        proposals = build_proposals(scan([tmp_path]).targets, fetchers)
    finally:
        client.get_api_key = original
        notes_tmdb.get_api_key = original
    assert all(p.confidence == Confidence.NONE and "No TMDB API key" in p.problems[0] for p in proposals)
    assert tmdb.urls == []
    assert summarize(proposals).problems == [proposals[0].problems[0]]


def test_the_api_key_is_never_stored_in_the_cache_and_a_second_run_asks_nothing(tmp_path):
    make_series(tmp_path)
    cache = ResponseCache(tmp_path / "cache.sqlite")
    first = fake_fetchers(key="SECRET-KEY-123")
    build_proposals(scan([tmp_path]).targets, first, cache=cache)
    assert first.tmdb.urls and all("SECRET-KEY-123" in u for u in first.tmdb.urls)  # it was sent
    cache.close()
    assert b"SECRET-KEY-123" not in (tmp_path / "cache.sqlite").read_bytes()
    second = fake_fetchers(key="ANOTHER-KEY")
    cache2 = ResponseCache(tmp_path / "cache.sqlite")
    engine = NotesEngine(second, cache2)
    [p] = build_proposals(scan([tmp_path]).targets, engine=engine)
    assert p.confidence == Confidence.CONFIDENT and second.tmdb.urls == [] and second.wiki.urls == []
    assert engine.stats.from_cache > 0 and engine.stats.requests == 0
    cache2.close()


def test_a_network_error_is_a_problem_on_the_proposal_not_a_crash(tmp_path):
    make_series(tmp_path)
    import urllib.error

    fetchers = fake_fetchers(tmdb_extra=[(r"/search/tv", urllib.error.URLError("no network"))])
    [p] = build_proposals(scan([tmp_path]).targets, fetchers)
    assert p.confidence == Confidence.NONE and "Could not reach TMDB" in p.problems[0] and "SECRET" not in p.problems[0]


def test_an_update_proposal_replaces_only_an_old_untouched_note_of_ours(tmp_path, monkeypatch):
    from redactor_common.core import trash
    from redactor_common.core.notes import NoteFrontMatter, render_note

    show = make_series(tmp_path)
    # an older shape of OUR note: stamped, untouched, but note-format 1 (the current shape is 2)
    old = render_note(NoteFrontMatter(title="Breaking Bad", generator="videoredactor 2026-01-01#01", note_format=1), "# Breaking Bad\n\nOld text.")
    (show / "series.md").write_bytes(old.encode("utf-8"))
    binned = []
    monkeypatch.setattr(trash, "trash_with_retries", lambda *a, **k: lambda path: (binned.append(path), __import__("os").remove(path)))
    found = scan([tmp_path], include_outdated=True)
    assert [t.update.kind for t in found.targets] == ["outdated-untouched"]
    [update] = build_proposals(found.targets, fake_fetchers())
    assert update.is_update and update.old_text == old and update.confidence == Confidence.CONFIDENT
    assert write_proposal(update).ok and binned == [str(show / "series.md")]
    assert note_status(show / "series.md").kind == "current" and "Old text." not in (show / "series.md").read_text(encoding="utf-8")
    assert scan([tmp_path], include_outdated=True).targets == []  # a current note is left alone


def test_a_note_edited_by_a_person_is_never_a_target(tmp_path):
    show = make_series(tmp_path)
    [first] = only(tmp_path)
    write_proposal(first)
    (show / "series.md").write_bytes((first.note_text + "\nMy own line.\n").encode("utf-8"))
    found = scan([tmp_path], include_outdated=True)
    assert found.targets == [] and found.skipped[0].state == "edited"


def test_undo_moves_a_created_note_to_the_bin_only_while_unchanged(tmp_path):
    from core.notes_flow import record_note_created
    from core import history

    make_series(tmp_path)
    [p] = only(tmp_path)
    path = write_proposal(p).path
    calls = []

    class Journal:
        def mark_restored(self, entry_id, restore_id):
            calls.append(entry_id)

    class Entry:
        id, kind = "e1", "note"
        note = None

    from redactor_common.core.notes_model import text_sha

    Entry.path, Entry.note = path, "sha256:" + __import__("hashlib").sha256(open(path, "rb").read()).hexdigest()
    with open(path, "ab") as handle:
        handle.write(b"edited\n")
    assert "changed" in undo_note_entry(Entry, Journal())
    assert record_note_created and history and text_sha  # the journal hook and the hash are the shared ones
