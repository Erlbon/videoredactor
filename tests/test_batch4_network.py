"""Batch 4 (2026-10-17): TMDB / TheTVDB / OpenSubtitles against hostile answers and a redirect to another host.
Real sockets (two local http.server instances on 127.0.0.1); nothing leaves the machine."""

import http.server
import json
import threading

import pytest

import core.opensubtitles_client as osub
import core.tmdb_client as tmdb
import core.tvdb_client as tvdb


class _Server:
    def __init__(self):
        self.routes = {}
        self.seen = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def _answer(self):
                outer.seen.append((self.command, self.path, {k.lower(): v for k, v in self.headers.items()}))
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                status, headers, body = outer.routes.get(self.path.split("?")[0], (404, {}, b"{}"))
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = _answer  # noqa: N815

            def log_message(self, *args):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def two_servers():
    first, second = _Server(), _Server()
    yield first, second
    first.close()
    second.close()


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    monkeypatch.setattr(tmdb, "_require_api_key", lambda: "k")
    monkeypatch.setattr(tvdb, "_require_api_key", lambda: "k")
    monkeypatch.setattr(osub, "_require_api_key", lambda: "SECRET-API-KEY")
    monkeypatch.setattr(tvdb, "_cached_token", "SECRET-JWT")


# -- credentials never follow a redirect to another host -------------------------------------------------------------


def test_tvdb_bearer_token_is_not_sent_to_the_host_a_redirect_points_at(two_servers, monkeypatch):
    first, second = two_servers
    monkeypatch.setattr(tvdb, "BASE_URL", first.base)
    first.routes["/series/1/episodes/default"] = (302, {"Location": f"{second.base}/landing"}, b"")
    second.routes["/landing"] = (200, {}, json.dumps({"data": {"episodes": [{"seasonNumber": 1, "number": 2, "name": "Two"}]}}).encode())
    episodes = tvdb.get_series_episodes(1)
    assert [(e.season_number, e.episode_number, e.name) for e in episodes] == [(1, 2, "Two")]
    assert first.seen[0][2]["authorization"] == "Bearer SECRET-JWT"
    assert second.seen and "authorization" not in second.seen[0][2]


def test_opensubtitles_api_key_and_login_token_are_not_sent_to_another_host(two_servers, monkeypatch):
    first, second = two_servers
    monkeypatch.setattr(osub, "BASE_URL", first.base)
    first.routes["/subtitles"] = (302, {"Location": f"{second.base}/landing"}, b"")
    second.routes["/landing"] = (200, {}, b'{"data": []}')
    assert osub._get_json("/subtitles", {"query": "x"}, bearer_token="SECRET-LOGIN") == {"data": []}
    assert first.seen[0][2]["api-key"] == "SECRET-API-KEY"
    seen = second.seen[0][2]
    assert "api-key" not in seen and "authorization" not in seen


def test_a_subtitle_download_still_follows_a_cdn_redirect_without_credentials(two_servers, monkeypatch):
    first, second = two_servers
    monkeypatch.setattr(osub, "BASE_URL", first.base)
    first.routes["/download"] = (200, {}, json.dumps({"link": f"{first.base}/file.srt"}).encode())
    first.routes["/file.srt"] = (302, {"Location": f"{second.base}/cdn/file.srt"}, b"")
    second.routes["/cdn/file.srt"] = (200, {}, b"1\n00:00:01,000 --> 00:00:02,000\nHello\n")
    assert "Hello" in osub.download_subtitle_text(5)
    assert second.seen and "api-key" not in second.seen[0][2] and "authorization" not in second.seen[0][2]


# -- hostile answers never reach the crash dialog -----------------------------------------------------------------------

DEEP = b"[" * 100000 + b"]" * 100000
BODIES = [
    b"[]", b"null", b"5", b'"text"', b"true", b'{"results": "x"}', b'{"results": [1, null, {}]}',
    b'{"data": "x"}', b'{"data": [1, null, {}]}', b'{"data": {"episodes": "x"}}', b'{"data": {"episodes": [1, null, {}]}}',
    b'{"data": [{"attributes": 5}, {"attributes": {"files": [1]}}, {"attributes": {"files": [{}]}}]}',
    b'{"data": [{"attributes": {"files": [{"file_id": {"a": 1}}]}}]}',
    b'{"credits": [], "genres": "x", "networks": [1], "seasons": [1, null, {}], "episodes": [1, {}], "external_ids": 3}',
    b'{"data": {"genres": [1, null], "originalNetwork": 5, "token": 7}}', DEEP,
]


def _calls(monkeypatch):
    """Every parsing entry point with a stand-in answer wired in by the caller."""
    return [
        (tmdb.TMDBError, lambda: tmdb.search_movies("x")),
        (tmdb.TMDBError, lambda: tmdb.search_tv("x")),
        (tmdb.TMDBError, lambda: tmdb.get_movie_details(1)),
        (tmdb.TMDBError, lambda: tmdb.get_tv_show_details(1)),
        (tmdb.TMDBError, lambda: tmdb.get_tv_seasons(1)),
        (tmdb.TMDBError, lambda: tmdb.get_season_episodes(1, 1)),
        (tmdb.TMDBError, lambda: tmdb.get_tv_episode_details(1, 1, 1)),
        (tvdb.TVDBError, lambda: tvdb.search_series("x")),
        (tvdb.TVDBError, lambda: tvdb.get_series_details(1)),
        (tvdb.TVDBError, lambda: tvdb.get_series_episodes(1)),
        (osub.OpenSubtitlesError, lambda: osub.search_by_title("x")),
        (osub.OpenSubtitlesError, lambda: osub.download_subtitle_text(1)),
    ]


@pytest.mark.parametrize("body", BODIES, ids=lambda b: b[:30].decode("latin-1") if len(b) < 1000 else "deep-nesting")
def test_a_hostile_answer_is_skipped_or_becomes_the_source_error(monkeypatch, body):
    monkeypatch.setattr(tmdb, "_fetch", lambda request: body)
    monkeypatch.setattr(tvdb, "_fetch", lambda request: body)
    monkeypatch.setattr(osub, "_fetch", lambda request: body)
    monkeypatch.setattr(osub, "_download_fetch", lambda request: body)
    for error_cls, call in _calls(monkeypatch):
        try:
            call()
        except error_cls:
            pass  # the source's own error: a friendly message, not a crash
        # any other exception (AttributeError, KeyError, TypeError, RecursionError) fails the test here


def test_good_answers_still_parse(monkeypatch):
    monkeypatch.setattr(tmdb, "_fetch", lambda request: json.dumps(
        {"results": [{"id": 3, "title": "T", "release_date": "2020", "overview": "o", "poster_path": "/p.jpg"}, 7, {"title": "no id"}]}).encode())
    found = tmdb.search_movies("t")
    assert [(c.tmdb_id, c.title, c.poster_path) for c in found] == [(3, "T", "/p.jpg")]
    monkeypatch.setattr(osub, "_fetch", lambda request: json.dumps({"data": [
        {"attributes": {"language": "en", "release": "R", "download_count": 9, "files": [{"file_id": 12}]}}, 4,
        {"attributes": {"files": []}}]}).encode())
    got = osub.search_by_title("t")
    assert [(c.file_id, c.language, c.release_name, c.download_count) for c in got] == [(12, "en", "R", 9)]
