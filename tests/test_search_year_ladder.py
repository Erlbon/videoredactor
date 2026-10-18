import json

from core import tmdb_client as tmdb
from core import tvdb_client as tvdb


def _record(monkeypatch, module, answers):
    """Replaces the module's HTTP fetch: returns the next canned answer and records each request's query string."""
    seen = []

    def fetch(request):
        seen.append(request.full_url)
        return json.dumps(answers[min(len(seen), len(answers)) - 1]).encode()

    monkeypatch.setattr(module, "_fetch", fetch)
    monkeypatch.setattr(module, "get_api_key", lambda: "k", raising=False)
    return seen


def test_tmdb_movie_year_ladder_goes_strict_then_loose_then_none(monkeypatch):
    seen = _record(monkeypatch, tmdb, [{"results": []}, {"results": []}, {"results": [{"id": 5, "title": "T"}]}])
    found = tmdb.search_movies("T", "1999")
    assert [c.tmdb_id for c in found] == [5]
    assert "primary_release_year=1999" in seen[0] and "year=1999" in seen[1] and "primary_release_year" not in seen[1]
    assert "year" not in seen[2]


def test_tmdb_movie_stops_at_the_first_step_with_results(monkeypatch):
    seen = _record(monkeypatch, tmdb, [{"results": [{"id": 5, "title": "T"}]}])
    tmdb.search_movies("T", "1999")
    assert len(seen) == 1


def test_tmdb_tv_uses_the_first_air_year_then_no_year(monkeypatch):
    seen = _record(monkeypatch, tmdb, [{"results": []}, {"results": [{"id": 2, "name": "S"}]}])
    assert [c.tmdb_id for c in tmdb.search_tv("S", "2008")] == [2]
    assert "first_air_date_year=2008" in seen[0] and "year" not in seen[1]
    assert len(_record(monkeypatch, tmdb, [{"results": [{"id": 2, "name": "S"}]}])) == 0
    seen = _record(monkeypatch, tmdb, [{"results": [{"id": 2, "name": "S"}]}])
    tmdb.search_tv("S")
    assert len(seen) == 1 and "year" not in seen[0]


def test_tvdb_year_is_tried_first_then_dropped(monkeypatch):
    seen = []

    def get_json(path, params, _retrying=False):
        seen.append(dict(params))
        return {"data": [] if "year" in params else [{"tvdb_id": 9, "name": "S"}]}

    monkeypatch.setattr(tvdb, "_get_json", get_json)
    assert [c.tvdb_id for c in tvdb.search_series("S", "2008")] == [9]
    assert seen[0]["year"] == "2008" and "year" not in seen[1]
