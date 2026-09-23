"""
TMDB / TheTVDB / OpenSubtitles HTTP handling via redactor_common's
lookup_client (2026-09-23). Each module's `_fetch` is replaced with a
fake, so nothing touches the network.
"""

import io
import json
import socket
import urllib.error

import pytest

import core.opensubtitles_client as osub
import core.tmdb_client as tmdb
import core.tvdb_client as tvdb


def _http_error(code):
    return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(b""))


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    monkeypatch.setattr(tmdb, "_require_api_key", lambda: "k")
    monkeypatch.setattr(tvdb, "_require_api_key", lambda: "k")
    monkeypatch.setattr(osub, "_require_api_key", lambda: "k")
    monkeypatch.setattr(tvdb, "_cached_token", None)


def test_tmdb_friendly_401_and_query_string(monkeypatch):
    seen = []

    def fake(request):
        seen.append(request.full_url)
        raise _http_error(401)

    monkeypatch.setattr(tmdb, "_fetch", fake)
    with pytest.raises(tmdb.TMDBError, match="rejected the API key"):
        tmdb._get_json("/search/movie", {"query": "Amélie"})
    assert "api_key=k" in seen[0] and "query=Am%C3%A9lie" in seen[0]


def test_tmdb_timeout_and_bad_json_become_tmdb_errors(monkeypatch):
    # Both used to escape as uncaught exceptions.
    monkeypatch.setattr(tmdb, "_fetch", lambda r: (_ for _ in ()).throw(socket.timeout("timed out")))
    with pytest.raises(tmdb.TMDBError, match="Could not reach TMDB"):
        tmdb._get_json("/x", {})
    monkeypatch.setattr(tmdb, "_fetch", lambda r: b"<html>not json")
    with pytest.raises(tmdb.TMDBError, match="unreadable"):
        tmdb._get_json("/x", {})


def test_tvdb_relogs_in_once_on_expired_token(monkeypatch):
    calls = []

    def fake(request):
        calls.append((request.get_method(), request.full_url))
        if request.full_url.endswith("/login"):
            assert json.loads(request.data) == {"apikey": "k"}
            return json.dumps({"data": {"token": f"t{len(calls)}"}}).encode()
        if len([c for c in calls if not c[1].endswith("/login")]) == 1:
            raise _http_error(401)
        return json.dumps({"data": {"ok": True}}).encode()

    monkeypatch.setattr(tvdb, "_fetch", fake)
    assert tvdb._get_json("/series/1") == {"data": {"ok": True}}
    assert [m for m, _ in calls] == ["POST", "GET", "POST", "GET"]


def test_tvdb_second_401_gives_up(monkeypatch):
    def fake(request):
        if request.full_url.endswith("/login"):
            return json.dumps({"data": {"token": "t"}}).encode()
        raise _http_error(401)

    monkeypatch.setattr(tvdb, "_fetch", fake)
    with pytest.raises(tvdb.TVDBError, match="even after re-login"):
        tvdb._get_json("/series/1")


def test_opensubtitles_sends_api_key_and_posts_json(monkeypatch):
    seen = []

    def fake(request):
        seen.append(request)
        return json.dumps({"link": "https://dl"}).encode()

    monkeypatch.setattr(osub, "_fetch", fake)
    monkeypatch.setattr(osub, "_download_fetch", lambda r: "Hej på dig".encode("utf-8"))
    assert osub.download_subtitle_text(5) == "Hej på dig"
    post = seen[0]
    assert post.get_method() == "POST" and post.get_header("Api-key") == "k"
    assert json.loads(post.data) == {"file_id": 5}
