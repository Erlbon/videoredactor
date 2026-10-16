"""core/theporndb_client.py against replies of the documented shape (synthetic content, injected fetch)."""

import logging
import re
import urllib.parse
import urllib.error

import pytest

from core import api_keys, theporndb_client as tp
from core.scene_lookup import MATCH_FILE, MATCH_TITLE
from tests.scene_fixtures import KEY, OSHASH, FakeFetch, http_error, tpdb_scene


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv("THEPORNDB_API_KEY", KEY)


@pytest.fixture
def fetch(monkeypatch, key):
    def install(*replies):
        fake = FakeFetch(*replies)
        monkeypatch.setattr(tp, "_fetch", fake)
        return fake

    monkeypatch.setattr(tp, "_sleep", lambda seconds: None)
    return install


def query_of(request) -> dict:
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(request.full_url).query))


def test_hash_match_is_a_file_match_with_every_field(fetch):
    fake = fetch({"data": tpdb_scene()})
    (result,) = tp.search_by_hash(OSHASH)
    request = fake.requests[0]
    assert urllib.parse.urlsplit(request.full_url).path == f"/scenes/hash/{OSHASH}"
    assert query_of(request) == {"type": "OSHASH"}
    assert request.get_header("Authorization") == f"Bearer {KEY}"
    assert re.fullmatch(r"TheVideoRedactor/\S+ \(https://github\.com/Erlbon\)", request.get_header("User-agent"))
    assert result.match_kind == MATCH_FILE and result.is_file_match
    assert (result.title, result.date, result.year, result.studio, result.parent_studio) == (
        "Test Scene Title", "2021-11-15", "2021", "Example Studio", "Example Network")
    assert result.performers == ["Performer One", "Performer Two"] and result.tags == ["Tag A", "Tag B"]
    assert result.director == "Director One" and result.code == "SKU-0001" and result.duration == 1830
    assert result.cover_url == "https://cdn.example.com/p/large.jpg"  # the poster sizes win
    assert result.urls == ["https://example.com/scene/test-scene-title"]
    assert result.page_url == "https://theporndb.net/scenes/example-studio-test-scene-title"
    assert result.external_id == "11111111-2222-3333-4444-555555555555"


def test_hash_search_tries_scenes_then_movies_then_jav_and_stops_at_the_first_hit(fetch):
    fake = fetch(http_error(404), {"data": tpdb_scene(type="Movie", slug="a-movie")})
    (result,) = tp.search_by_hash(OSHASH)
    paths = [urllib.parse.urlsplit(r.full_url).path for r in fake.requests]
    assert paths == [f"/scenes/hash/{OSHASH}", f"/movies/hash/{OSHASH}"]
    assert result.page_url == "https://theporndb.net/movies/a-movie"


def test_no_hash_match_is_an_empty_list_not_an_error(fetch):
    fake = fetch(http_error(404))
    assert tp.search_by_hash(OSHASH) == []
    assert len(fake.requests) == 3  # scenes, movies, jav


def test_an_invalid_hash_makes_no_request(fetch):
    fake = fetch({"data": tpdb_scene()})
    assert tp.search_by_hash("") == [] and tp.search_by_hash("not-a-hash") == [] and tp.search_by_hash("12345") == []
    assert fake.requests == []


def test_title_search_sends_q_and_year_and_marks_title_matches(fetch):
    fake = fetch({"data": [tpdb_scene(), tpdb_scene(id="other", title="Another One", slug="another-one")],
                  "meta": {"total": 2}})
    results = tp.search_by_title("Test Scene Title", "2021")
    request = fake.requests[0]
    assert urllib.parse.urlsplit(request.full_url).path == "/scenes"
    assert query_of(request) == {"q": "Test Scene Title", "per_page": "25", "year": "2021"}
    assert [r.title for r in results] == ["Test Scene Title", "Another One"]
    assert all(r.match_kind == MATCH_TITLE and not r.is_file_match for r in results)


def test_title_search_of_blank_text_asks_nothing_and_a_bad_year_is_left_out(fetch):
    fake = fetch({"data": []})
    assert tp.search_by_title("   ") == []
    assert fake.requests == []
    tp.search_by_title("x", "20x1")
    assert "year" not in query_of(fake.requests[0])


def test_empty_result(fetch):
    fetch({"data": [], "meta": {"total": 0}})
    assert tp.search_by_title("Nothing Here") == []


def test_missing_and_odd_fields_are_survived(fetch):
    fetch({"data": [
        {"id": "only-an-id"},
        {"title": "Only A Title", "site": None, "performers": None, "tags": "nope", "date": None, "posters": []},
        {"title": "Odd", "performers": [None, "text", {"name": None}, {"name": " Spaced   Name "}], "date": "2021-11-15T10:00:00Z",
         "duration": "long", "image": "javascript:alert(1)", "description": {"x": 1}},
        None, "text", 5, {},
    ]})
    results = tp.search_by_title("x")
    assert [r.title for r in results] == ["", "Only A Title", "Odd"]
    bare, titled, odd = results
    assert bare.external_id == "only-an-id" and bare.performers == [] and bare.cover_url == ""
    assert titled.studio == "" and titled.tags == [] and titled.date == ""
    assert odd.performers == ["Spaced Name"] and odd.date == "2021-11-15" and odd.duration is None
    assert odd.cover_url == "" and odd.description == ""  # a non-http address and a non-string are dropped


def test_unicode_survives(fetch):
    fetch(('{"data": [{"id": "u", "title": "Ünï Tëst Título 日本語", "site": {"name": "Étude Ström"}}]}').encode("utf-8"))
    (result,) = tp.search_by_title("日本語")
    assert result.title == "Ünï Tëst Título 日本語" and result.studio == "Étude Ström"


def test_control_characters_never_reach_a_field(fetch):
    fetch({"data": [tpdb_scene(title="Bad\x00Ti\x07tle\n", description="a\x00b\r\nc")]})
    (result,) = tp.search_by_title("x")
    assert result.title == "BadTitle" and result.description == "ab\nc"


def test_get_one_record(fetch):
    fake = fetch({"data": tpdb_scene()})
    assert tp.get("example-studio-test-scene-title").title == "Test Scene Title"
    assert urllib.parse.urlsplit(fake.requests[0].full_url).path == "/scenes/example-studio-test-scene-title"
    fetch(http_error(404))
    with pytest.raises(tp.ThePornDBError, match="no scene"):
        tp.get("missing")
    with pytest.raises(tp.ThePornDBError):
        tp.get("../etc")


# --- errors ------------------------------------------------------------------------------------------------------


def test_missing_key_says_where_to_get_one_and_asks_nothing(monkeypatch):
    monkeypatch.delenv("THEPORNDB_API_KEY", raising=False)
    fake = FakeFetch({"data": []})
    monkeypatch.setattr(tp, "_fetch", fake)
    with pytest.raises(tp.ThePornDBAuthError) as info:
        tp.search_by_title("x")
    assert "https://theporndb.net/user/api-tokens" in str(info.value) and "Tools > API Keys" in str(info.value)
    assert fake.requests == []


def test_a_rejected_key_is_401_and_the_message_never_holds_the_key(fetch):
    fetch(http_error(401, b'{"message":"Unauthenticated."}'))
    with pytest.raises(tp.ThePornDBAuthError) as info:
        tp.search_by_title("x")
    assert "HTTP 401" in str(info.value) and KEY not in str(info.value)


def test_429_is_retried_and_then_succeeds(fetch, monkeypatch):
    delays = []
    monkeypatch.setattr(tp, "_sleep", delays.append)
    fake = fetch(http_error(429), http_error(429), {"data": [tpdb_scene()]})
    assert len(tp.search_by_title("x")) == 1
    assert len(fake.requests) == 3 and delays == [2.0, 5.0]


def test_429_that_stays_is_a_rate_limit_error(fetch):
    fetch(http_error(429))
    with pytest.raises(tp.ThePornDBRateLimited, match="rate limit"):
        tp.search_by_title("x")


def test_network_down_is_a_clear_message_without_the_key(fetch):
    fetch(urllib.error.URLError(f"[Errno 11001] getaddrinfo failed for {KEY}"))
    with pytest.raises(tp.ThePornDBError) as info:
        tp.search_by_title("x")
    text = str(info.value)
    assert text.startswith("Could not reach ThePornDB") and "internet connection" in text and KEY not in text


def test_a_garbled_answer_and_a_server_error(fetch):
    fetch(b"<html>oops</html>")
    with pytest.raises(tp.ThePornDBError, match="unreadable"):
        tp.search_by_title("x")
    fetch(http_error(500))
    with pytest.raises(tp.ThePornDBError, match="HTTP 500"):
        tp.search_by_title("x")
    fetch(b'["a list"]')
    with pytest.raises(tp.ThePornDBError, match="unexpected"):
        tp.search_by_title("x")


def test_the_key_is_in_no_log_line_and_no_url(fetch, caplog):
    fake = fetch({"data": [tpdb_scene()]}, http_error(401), urllib.error.URLError(KEY))
    with caplog.at_level(logging.DEBUG):
        tp.search_by_title("x")
        for call in (lambda: tp.search_by_title("x"), lambda: tp.search_by_hash(OSHASH)):
            with pytest.raises(tp.ThePornDBError) as info:
                call()
            assert KEY not in str(info.value) and KEY not in repr(info.value)
    assert KEY not in caplog.text
    assert all(KEY not in request.full_url for request in fake.requests)  # it travels in a header only


def test_pace_is_at_least_a_second():
    assert tp._throttle.interval >= 1.0


# --- the key check ---------------------------------------------------------------------------------------------


def test_check_key_ok_and_with_a_typed_key(fetch, monkeypatch):
    fake = fetch({"id": 123, "name": "Example User"})
    assert tp.check_key() == "OK: ThePornDB accepted the key (account: Example User)."
    assert urllib.parse.urlsplit(fake.requests[0].full_url).path == "/auth/user"
    monkeypatch.delenv("THEPORNDB_API_KEY")
    assert "OK" in tp.check_key("typed-but-unsaved")
    assert fake.requests[-1].get_header("Authorization") == "Bearer typed-but-unsaved"


def test_check_key_reports_the_exact_http_reason(fetch):
    fetch(http_error(401))
    with pytest.raises(tp.ThePornDBError, match="HTTP 401 Unauthorized"):
        tp.check_key()
    fetch(http_error(403))
    with pytest.raises(tp.ThePornDBError, match="HTTP 403"):
        tp.check_key()


def test_key_comes_from_the_secret_store_too(monkeypatch):
    monkeypatch.delenv("THEPORNDB_API_KEY", raising=False)
    from redactor_common.core import secret_store

    secret_store.set_secret(api_keys.APP, api_keys.KEYS["theporndb"][0], "stored-key")
    assert tp.get_api_key() == "stored-key"


# --- the cover (only ever on request) ---------------------------------------------------------------------------------


def test_cover_download_sends_no_key_and_refuses_other_schemes(monkeypatch, key):
    fake = FakeFetch(b"\xff\xd8\xffJPEGDATA")
    monkeypatch.setattr(tp, "_cover_fetch", fake)
    assert tp.download_cover("https://cdn.example.com/p/large.jpg").startswith(b"\xff\xd8\xff")
    assert fake.requests[0] == "https://cdn.example.com/p/large.jpg"  # a plain URL: no header can carry the key
    for bad in ("file:///C:/secret.jpg", "ftp://x/y.jpg", "", "javascript:alert(1)"):
        with pytest.raises(tp.ThePornDBError):
            tp.download_cover(bad)
    assert len(fake.requests) == 1


def test_a_search_never_downloads_an_image(fetch, monkeypatch):
    cover = FakeFetch(b"x")
    monkeypatch.setattr(tp, "_cover_fetch", cover)
    fetch({"data": [tpdb_scene()]})
    tp.search_by_title("x")
    tp.search_by_hash(OSHASH)
    assert cover.requests == []
