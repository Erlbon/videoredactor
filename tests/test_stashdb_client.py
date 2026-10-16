"""core/stashdb_client.py against replies of the documented GraphQL shape (synthetic content, injected fetch)."""

import logging
import urllib.error

import pytest

from core import scene_lookup, stashdb_client as sd
from core.scene_lookup import MATCH_FILE, MATCH_TITLE
from tests.scene_fixtures import KEY, MD5, OSHASH, FakeFetch, http_error, stash_scene

NOT_AUTH = {"errors": [{"message": "not authorized", "path": ["me"]}], "data": {"me": None}}


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv("STASHDB_API_KEY", KEY)


@pytest.fixture
def fetch(monkeypatch, key):
    def install(*replies):
        fake = FakeFetch(*replies)
        monkeypatch.setattr(sd, "_fetch", fake)
        return fake

    monkeypatch.setattr(sd, "_sleep", lambda seconds: None)
    return install


def test_hash_match_posts_the_fingerprint_and_maps_every_field(fetch):
    fake = fetch({"data": {"findScenesBySceneFingerprints": [[stash_scene()]]}})
    (result,) = sd.search_by_hash(OSHASH)
    request = fake.requests[0]
    assert request.full_url == "https://stashdb.org/graphql" and request.get_method() == "POST"
    assert request.get_header("Apikey") == KEY
    assert request.get_header("User-agent").startswith("TheVideoRedactor/")
    body = fake.body()
    assert body["variables"] == {"fp": [[{"hash": OSHASH, "algorithm": "OSHASH"}]]}
    assert "findScenesBySceneFingerprints" in body["query"] and "release_date" in body["query"]
    assert KEY not in request.data.decode("utf-8")  # the key is a header, never in the body
    assert result.match_kind == MATCH_FILE
    assert (result.title, result.date, result.studio, result.parent_studio) == (
        "Test Scene Title", "2021-11-15", "Example Studio", "Example Network")
    assert result.performers == ["Credited Alias", "Performer Two"]  # as credited, no disambiguation
    assert result.tags == ["Tag A", "Tag B"] and result.director == "Director One" and result.code == "CODE-1"
    assert result.cover_url == "https://cdn.example.com/s/one.jpg" and result.duration == 1830
    assert result.page_url == "https://stashdb.org/scenes/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert result.urls == ["https://example.com/scene/test-scene-title"]


def test_md5_joins_the_same_file_group_and_the_flattened_answer_has_no_duplicates(fetch):
    fake = fetch({"data": {"findScenesBySceneFingerprints": [[stash_scene(), stash_scene()], []]}})
    results = sd.search_by_hash(OSHASH, MD5)
    assert fake.body()["variables"]["fp"] == [[{"hash": OSHASH, "algorithm": "OSHASH"}, {"hash": MD5, "algorithm": "MD5"}]]
    assert len(results) == 1


def test_no_hash_match_and_invalid_hashes(fetch):
    fake = fetch({"data": {"findScenesBySceneFingerprints": [[]]}})
    assert sd.search_by_hash(OSHASH) == []
    n = len(fake.requests)
    assert sd.search_by_hash("zz") == [] and sd.search_by_hash("", "short") == []
    assert len(fake.requests) == n  # nothing valid to ask: no request


def test_title_search_uses_searchScenes_and_puts_the_year_first(fetch):
    older = stash_scene(id="old", title="Same Name", release_date="2019-01-01")
    wanted = stash_scene(id="new", title="Same Name", release_date="2021-05-05")
    fake = fetch({"data": {"searchScenes": {"count": 2, "scenes": [older, wanted]}}})
    results = sd.search_by_title("Same Name", "2021")
    assert [r.external_id for r in results] == ["new", "old"]
    assert all(r.match_kind == MATCH_TITLE for r in results)
    assert fake.body()["variables"] == {"term": "Same Name", "limit": 25}
    assert "searchScenes" in fake.body()["query"]
    assert sd.search_by_title("  ") == []


def test_an_older_server_falls_back_to_date_and_searchScene(fetch):
    unknown = {"errors": [{"message": 'Cannot query field "release_date" on type "Scene".'}]}
    no_search = {"errors": [{"message": 'Cannot query field "searchScenes" on type "Query".'}]}
    older = stash_scene(date="2020-02-02")
    del older["release_date"]
    fake = fetch(unknown, no_search, no_search, {"data": {"searchScene": [older]}})
    (result,) = sd.search_by_title("x")
    assert result.date == "2020-02-02"
    assert [("searchScenes" in r["query"], "release_date" in r["query"]) for r in map(lambda i: fake.body(i), range(4))] == [
        (True, True), (True, False), (False, True), (False, False)]


def test_empty_result_and_partial_data(fetch):
    fetch({"data": {"searchScenes": {"count": 0, "scenes": []}}})
    assert sd.search_by_title("x") == []
    fetch({"data": {"searchScenes": {"scenes": [stash_scene(studio=None, performers=None, tags=None, images=None,
                                                             urls=None, release_date=None, details=None), None, "x", {}]}}})
    (result,) = sd.search_by_title("x")
    assert (result.studio, result.performers, result.tags, result.cover_url, result.date, result.description) == ("", [], [], "", "", "")


def test_unicode_and_control_characters(fetch):
    fetch(('{"data": {"searchScenes": {"scenes": [{"id": "u", "title": "Ünï\\u0000 Tëst 日本語", "details": "Ström"}]}}}').encode("utf-8"))
    (result,) = sd.search_by_title("日本語")
    assert result.title == "Ünï Tëst 日本語" and result.description == "Ström"


def test_get_one_scene(fetch):
    fake = fetch({"data": {"findScene": stash_scene()}})
    assert sd.get("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee").title == "Test Scene Title"
    assert fake.body()["variables"] == {"id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}
    fetch({"data": {"findScene": None}})
    with pytest.raises(sd.StashDBError, match="no scene"):
        sd.get("missing")


# --- the endpoint -----------------------------------------------------------------------------------------------------


def test_the_endpoint_is_a_setting_and_the_page_links_follow_it(fetch):
    assert scene_lookup.set_stash_endpoint("https://box.example.org/") == "https://box.example.org/graphql"
    fake = fetch({"data": {"searchScenes": {"scenes": [stash_scene()]}}})
    (result,) = sd.search_by_title("x")
    assert fake.requests[0].full_url == "https://box.example.org/graphql"
    assert result.page_url == "https://box.example.org/scenes/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    scene_lookup.set_stash_endpoint(scene_lookup.DEFAULT_STASH_ENDPOINT)
    assert scene_lookup.stash_endpoint() == "https://stashdb.org/graphql"


# --- errors ------------------------------------------------------------------------------------------------------------


def test_missing_key_says_where_to_get_one_and_asks_nothing(monkeypatch):
    monkeypatch.delenv("STASHDB_API_KEY", raising=False)
    fake = FakeFetch({"data": {}})
    monkeypatch.setattr(sd, "_fetch", fake)
    with pytest.raises(sd.StashDBAuthError) as info:
        sd.search_by_title("x")
    assert "https://stashdb.org/register" in str(info.value) and "Tools > API Keys" in str(info.value)
    assert fake.requests == []


def test_a_rejected_key_is_http_401_with_an_empty_body(fetch):
    fetch(http_error(401))
    with pytest.raises(sd.StashDBAuthError) as info:
        sd.search_by_title("x")
    assert "HTTP 401" in str(info.value) and KEY not in str(info.value)


def test_a_missing_key_at_the_server_is_http_200_with_not_authorized(fetch):
    fetch(NOT_AUTH)
    with pytest.raises(sd.StashDBAuthError, match="did not authorize"):
        sd.search_by_title("x")


def test_a_graphql_error_is_shown_with_the_key_scrubbed(fetch):
    fetch({"errors": [{"message": f"internal error for {KEY}"}], "data": None})
    with pytest.raises(sd.StashDBError) as info:
        sd.search_by_title("x")
    assert "internal error" in str(info.value) and KEY not in str(info.value)


def test_429_retry_then_success_and_a_lasting_429(fetch, monkeypatch):
    delays = []
    monkeypatch.setattr(sd, "_sleep", delays.append)
    fetch(http_error(429), {"data": {"searchScenes": {"scenes": [stash_scene()]}}})
    assert len(sd.search_by_title("x")) == 1 and delays == [2.0]
    fetch(http_error(429))
    with pytest.raises(sd.StashDBRateLimited):
        sd.search_by_title("x")


def test_network_down_message(fetch):
    fetch(urllib.error.URLError(f"timed out talking to {KEY}"))
    with pytest.raises(sd.StashDBError) as info:
        sd.search_by_title("x")
    text = str(info.value)
    assert text.startswith("Could not reach stashdb.org") and "internet connection" in text and KEY not in text


def test_a_garbled_answer(fetch):
    fetch(b"not json")
    with pytest.raises(sd.StashDBError, match="unreadable"):
        sd.search_by_title("x")


def test_the_key_is_in_no_log_line(fetch, caplog):
    fetch(NOT_AUTH, http_error(401), urllib.error.URLError(KEY), {"errors": [{"message": KEY}]})
    with caplog.at_level(logging.DEBUG):
        for _ in range(4):
            with pytest.raises(sd.StashDBError) as info:
                sd.search_by_title("x")
            assert KEY not in str(info.value) and KEY not in repr(info.value)
    assert KEY not in caplog.text


def test_pace_is_at_least_a_second():
    assert sd._throttle.interval >= 1.0


# --- the key check ------------------------------------------------------------------------------------------------------


def test_check_key_ok(fetch):
    fake = fetch({"data": {"me": {"name": "Example User"}}})
    assert sd.check_key() == "OK: stashdb.org accepted the key (account: Example User)."
    assert fake.body()["query"].strip() == "{ me { name } }"


def test_check_key_failures_are_exact(fetch):
    fetch(NOT_AUTH)
    with pytest.raises(sd.StashDBAuthError):
        sd.check_key()
    fetch(http_error(401))
    with pytest.raises(sd.StashDBAuthError, match="HTTP 401 Unauthorized"):
        sd.check_key()
    fetch({"data": {"me": None}})
    with pytest.raises(sd.StashDBAuthError, match="did not recognise"):
        sd.check_key()


def test_check_key_with_typed_key_and_endpoint(fetch, monkeypatch):
    fake = fetch({"data": {"me": {"name": "N"}}})
    monkeypatch.delenv("STASHDB_API_KEY")
    assert "box.example.org" in sd.check_key("typed", "https://box.example.org/graphql")
    assert fake.requests[0].get_header("Apikey") == "typed" and fake.requests[0].full_url == "https://box.example.org/graphql"


# --- the cover ----------------------------------------------------------------------------------------------------------


def test_cover_key_goes_only_to_the_endpoints_own_host(monkeypatch, key):
    fake = FakeFetch(b"\xff\xd8\xffA", b"\xff\xd8\xffB")
    monkeypatch.setattr(sd, "_cover_fetch", fake)
    sd.download_cover("https://cdn.example.com/s/one.jpg")
    assert fake.requests[0] == "https://cdn.example.com/s/one.jpg"  # a plain string: no header, no key
    sd.download_cover("https://stashdb.org/images/one")
    assert fake.requests[1].get_header("Apikey") == KEY
    with pytest.raises(sd.StashDBError):
        sd.download_cover("file:///C:/x.jpg")


def test_a_search_never_downloads_an_image(fetch, monkeypatch):
    cover = FakeFetch(b"x")
    monkeypatch.setattr(sd, "_cover_fetch", cover)
    fetch({"data": {"searchScenes": {"scenes": [stash_scene()]}}})
    sd.search_by_title("x")
    assert cover.requests == []
