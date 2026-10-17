"""core/scene_lookup.py: the field mapping, the settings, the file hashes, the polite HTTP pieces, the CLI key check
and the settings bundle (which must carry the options and never a key)."""

import hashlib
import json
import os
import threading
import urllib.request

import pytest

from core import api_keys, scene_lookup as sl, settings_adapter
from core.opensubtitles_hash import compute_moviehash
from core.scene_lookup import SceneResult
from core.video_metadata import ContentType
from tests.scene_fixtures import KEY, FakeFetch, http_error, stash_scene, tpdb_scene


def result(**over) -> SceneResult:
    base = dict(
        source="StashDB", external_id="abc", title="Test Scene Title", date="2021-11-15", studio="Example Studio",
        parent_studio="Example Network", performers=["Performer One", "Performer Two"], tags=["Tag A", "Tag B"],
        description="Synthetic.", director="Director One", page_url="https://stashdb.org/scenes/abc",
    )
    base.update(over)
    return SceneResult(**base)


# --- the mapping (the one place) ------------------------------------------------------------------------------------------


def test_field_mapping_table():
    assert sl.scene_fields(result()) == {
        "title": "Test Scene Title", "release_date": "2021-11-15", "description": "Synthetic.",
        "studio": "Example Studio", "collection": "Example Network", "cast": "Performer One, Performer Two",
        "genre_tags": "Tag A, Tag B", "director": "Director One", "comment": "StashDB: https://stashdb.org/scenes/abc",
    }
    assert sl.CONTENT_TYPE is ContentType.PORN  # adult scenes are their own content type


def test_empty_values_are_left_out_and_a_parentless_studio_has_no_collection():
    fields = sl.scene_fields(result(parent_studio="", description="", director="", tags=[], date=""))
    assert set(fields) == {"title", "studio", "cast", "comment"}
    assert sl.scene_fields(SceneResult(source="X", external_id="")) == {}
    assert "collection" not in sl.scene_fields(result(studio="", parent_studio="Orphan Parent"))


def test_the_comment_keeps_what_is_there_and_is_not_repeated():
    line = "StashDB: https://stashdb.org/scenes/abc"
    assert sl.scene_fields(result(), "my note")["comment"] == f"my note\n{line}"
    assert "comment" not in sl.scene_fields(result(), f"my note\n{line}")
    assert sl.scene_fields(result(page_url=""))["comment"] == "StashDB: id abc"


def test_commas_and_line_breaks_cannot_break_a_comma_separated_field():
    fields = sl.scene_fields(result(performers=["Doe, Jane", "Doe, Jane", "  "], tags=["a\nb"], title="Two\nLines"))
    assert fields["cast"] == "Doe Jane" and fields["genre_tags"] == "a b" and fields["title"] == "Two Lines"


def test_dates_and_years():
    assert sl.clean_date("2021-11-15T10:00:00Z") == "2021-11-15" and sl.clean_date("2021") == "2021"
    assert sl.clean_date("2021-11") == "2021-11" and sl.clean_date("yesterday") == "" and sl.clean_date(None) == ""
    assert result(date="2021-11-15").year == "2021" and result(date="").year == ""


def test_only_http_urls_pass():
    assert sl.clean_url("https://a.example/x") and sl.clean_url("http://a.example/x")
    assert sl.clean_url("file:///C:/x") == "" and sl.clean_url("javascript:alert(1)") == "" and sl.clean_url(5) == ""


# --- the settings -------------------------------------------------------------------------------------------------------


def test_endpoint_normalisation():
    assert sl.normalize_endpoint("https://stashdb.org") == "https://stashdb.org/graphql"
    assert sl.normalize_endpoint(" HTTPS://Box.Example.org/graphql/ ") == "https://Box.Example.org/graphql"
    assert sl.normalize_endpoint("http://localhost:9999/graphql") == "http://localhost:9999/graphql"
    for bad in ("", "stashdb.org", "ftp://x/graphql", "file:///x", "https://", "https://user:pw@x.example/graphql",
                "https://x.example/graphql?apikey=1"):
        with pytest.raises(ValueError):
            sl.normalize_endpoint(bad)


def test_a_hand_edited_bad_endpoint_falls_back_to_the_default():
    from core import config

    config.set_setting(sl.SECTION, "stashdb_endpoint", "file:///etc/passwd")
    assert sl.stash_endpoint() == sl.DEFAULT_STASH_ENDPOINT


def test_md5_is_off_by_default_and_switchable():
    assert sl.md5_enabled() is False
    sl.set_md5_enabled(True)
    assert sl.md5_enabled() is True
    sl.set_md5_enabled(False)
    assert sl.md5_enabled() is False


# --- the hashes ---------------------------------------------------------------------------------------------------------


@pytest.fixture
def big_file(tmp_path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(os.urandom(300 * 1024))
    return path


def test_oshash_is_the_moviehash_in_16_hex_characters(big_file):
    hashes = sl.file_hashes(str(big_file))
    assert hashes.oshash == compute_moviehash(str(big_file)) and sl.valid_oshash(hashes.oshash)
    assert len(hashes.oshash) == 16 and hashes.oshash == hashes.oshash.lower()
    assert hashes.md5 == ""  # never computed unless asked


def test_a_file_under_128_kb_has_no_oshash(tmp_path):
    small = tmp_path / "small.mp4"
    small.write_bytes(b"x" * 1000)
    assert sl.file_hashes(str(small)).oshash == ""


def test_md5_reads_the_whole_file_reports_progress_and_can_be_cancelled(big_file):
    expected = hashlib.md5(big_file.read_bytes()).hexdigest()  # noqa: S324
    seen = []
    assert sl.compute_md5(str(big_file), progress=lambda done, total: seen.append((done, total))) == expected
    assert seen[-1] == (300 * 1024, 300 * 1024)
    assert sl.file_hashes(str(big_file), with_md5=True).md5 == expected
    stop = threading.Event()
    stop.set()
    assert sl.compute_md5(str(big_file), cancel=stop) == ""


# --- the search text ------------------------------------------------------------------------------------------------------


def test_search_text_from_a_file_name():
    assert sl.search_text_for("Example.Studio.Test.Scene.Title.2021.1080p.WEB-DL.x264") == ("Example Studio Test Scene Title", "2021")
    text, year = sl.search_text_for("plain name")
    assert text == "plain name" and year == ""


# --- redirects never carry the key to another host ----------------------------------------------------------------------------


def test_redirects_stay_on_the_same_site():
    handler = sl.SameSiteRedirects()
    request = urllib.request.Request("https://api.example.com/scenes", headers={"Authorization": "Bearer k"})
    other = handler.redirect_request(request, None, 302, "Found", {}, "https://evil.example.net/steal")
    assert other is None
    downgrade = handler.redirect_request(request, None, 302, "Found", {}, "http://api.example.com/scenes")
    assert downgrade is None
    same = handler.redirect_request(request, None, 302, "Found", {}, "https://api.example.com/other")
    assert same is not None and same.full_url == "https://api.example.com/other"


# --- the command line key check -------------------------------------------------------------------------------------------------


def run_cli(*argv):
    from videocli.main import main

    return main(list(argv))


def test_lookup_check_prints_ok_and_exits_0(monkeypatch, capsys):
    from core import theporndb_client as tp

    monkeypatch.setenv("THEPORNDB_API_KEY", KEY)
    monkeypatch.setattr(tp, "_fetch", FakeFetch({"id": 1, "name": "Example User"}))
    assert run_cli("lookup-check", "theporndb") == 0
    out = capsys.readouterr().out
    assert out.startswith("OK: ThePornDB accepted the key") and KEY not in out


def test_lookup_check_prints_the_exact_reason_and_exits_1(monkeypatch, capsys):
    from core import stashdb_client as sd

    monkeypatch.setenv("STASHDB_API_KEY", KEY)
    monkeypatch.setattr(sd, "_fetch", FakeFetch(http_error(401)))
    assert run_cli("lookup-check", "stashdb") == 1
    out = capsys.readouterr().out
    assert "FAILED" in out and "HTTP 401 Unauthorized" in out and KEY not in out


def test_lookup_check_without_a_key_says_so(capsys):
    assert run_cli("lookup-check", "stashdb") == 1
    assert "No StashDB API key" in capsys.readouterr().out


def test_lookup_check_json_and_endpoint(monkeypatch, capsys):
    from core import stashdb_client as sd

    monkeypatch.setenv("STASHDB_API_KEY", KEY)
    fake = FakeFetch({"data": {"me": {"name": "N"}}})
    monkeypatch.setattr(sd, "_fetch", fake)
    assert run_cli("lookup-check", "stashdb", "--endpoint", "https://box.example.org", "--json") == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["ok"] is True and doc["results"][0]["service"] == "stashdb"
    assert fake.requests[0].full_url == "https://box.example.org/graphql"


def test_lookup_check_refuses_a_bad_endpoint_and_one_for_theporndb():
    from redactor_common.cli import CliError

    with pytest.raises(CliError, match="must start with https"):
        run_cli("lookup-check", "stashdb", "--endpoint", "ftp://x")
    with pytest.raises(CliError, match="stashdb only"):
        run_cli("lookup-check", "theporndb", "--endpoint", "https://x.example")


# --- the settings bundle: options and the address travel, keys never ----------------------------------------------------------


def test_bundle_carries_the_endpoint_and_md5_but_no_key(monkeypatch):
    from redactor_common.core import secret_store

    secret_store.set_secret(api_keys.APP, api_keys.KEYS["theporndb"][0], KEY)
    secret_store.set_secret(api_keys.APP, api_keys.KEYS["stashdb"][0], KEY)
    sl.set_stash_endpoint("https://box.example.org/graphql")
    sl.set_md5_enabled(True)
    adapter = settings_adapter.VideoSettingsAdapter()
    values = adapter.read_section("scene_lookup")
    assert values == {"stashdb_endpoint": "https://box.example.org/graphql", "md5": True}
    everything = json.dumps({k: adapter.read_section(k) for k in settings_adapter.SECTIONS})
    assert KEY not in everything and "api_key" not in everything.lower()
    assert "scene_lookup" in [s.key for s in adapter.sections()]


def test_importing_the_options_validates_the_address():
    adapter = settings_adapter.VideoSettingsAdapter()
    adapter.write_section("scene_lookup", {"stashdb_endpoint": "https://other.example.org", "md5": True})
    assert sl.stash_endpoint() == "https://other.example.org/graphql" and sl.md5_enabled()
    with pytest.raises(ValueError):
        adapter.write_section("scene_lookup", {"stashdb_endpoint": "file:///etc/passwd"})
    assert sl.stash_endpoint() == "https://other.example.org/graphql"  # the bad value changed nothing
    adapter.write_section("scene_lookup", {"stashdb_endpoint": sl.DEFAULT_STASH_ENDPOINT})
    assert sl.stash_endpoint() == sl.DEFAULT_STASH_ENDPOINT


def test_the_two_services_have_key_entries_and_env_vars():
    assert api_keys.KEYS["theporndb"][1] == "THEPORNDB_API_KEY" and api_keys.KEYS["stashdb"][1] == "STASHDB_API_KEY"


def test_porn_is_a_content_type_with_the_movie_fields():
    from core.video_metadata import TYPE_SPECIFIC_FIELDS, fields_for_content_type
    assert ContentType("Porn") is ContentType.PORN
    assert fields_for_content_type(ContentType.PORN) == fields_for_content_type(ContentType.MOVIE)
    assert TYPE_SPECIFIC_FIELDS[ContentType.PORN]
