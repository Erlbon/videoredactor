"""The source-id tags (core/source_tags.py, core/lookup_sources.py): ids <-> tag text, what a lookup writes, METADATA_MATCH,
the read-only fields, and that no settings export carries any of it."""

import datetime

import pytest

from redactor_common.core.source_ids import SourceRef

from core import lookup_sources, mkv_backend, mp4_backend, source_tags
from core.scene_lookup import SceneResult
from core.video_metadata import EDITABLE_FIELDS, SOURCE_FIELDS, VideoMetadata

U = "d284e2a0-789d-4e6c-beb9-6e61c24e8b84"
DAY = datetime.date(2026, 10, 16)


def test_tag_names_are_the_documented_ones_in_every_backend():
    expected = {"theporndb_id": "THEPORNDB_ID", "stashdb_id": "STASHDB_ID", "tmdb_id": "TMDB_ID", "imdb_id": "IMDB_ID",
                "tvdb_id": "TVDB_ID", "source_url": "SOURCE_URL", "metadata_match": "METADATA_MATCH"}
    assert source_tags.TAG_NAMES == expected == mp4_backend.SOURCE_TAG_NAMES
    for field, name in expected.items():
        assert mkv_backend.FIELD_TO_MKV_TAG[field] == name
        assert mp4_backend._custom_key(field) == f"----:com.videoredactor:{name}"
    assert set(SOURCE_FIELDS) == set(expected)


def test_source_fields_are_not_editable_and_do_not_count_as_a_metadata_difference():
    assert not set(SOURCE_FIELDS) & set(EDITABLE_FIELDS)
    assert VideoMetadata(tmdb_id="movie:1") == VideoMetadata()  # compare=False, like scan_stamp
    assert VideoMetadata(cover_pending=b"x") != VideoMetadata()  # a staged poster IS an undoable change


@pytest.mark.parametrize("ref,field,text", [
    (SourceRef("imdb", "title", "tt0133093"), "imdb_id", "tt0133093"),
    (SourceRef("theporndb", "scene", U), "theporndb_id", U),
    (SourceRef("theporndb", "movie", U), "theporndb_id", "movie:" + U),
    (SourceRef("stashdb", "scene", U), "stashdb_id", U),
    (SourceRef("tmdb", "movie", "603"), "tmdb_id", "movie:603"),
    (SourceRef("tmdb", "tv", "1396"), "tmdb_id", "tv:1396"),
    (SourceRef("tmdb", "episode", "1396/1/2"), "tmdb_id", "episode:1396/1/2"),
    (SourceRef("tvdb", "series", "73244"), "tvdb_id", "series:73244"),
])
def test_ids_round_trip_through_their_tag_text(ref, field, text):
    assert source_tags.encode_ref(ref) == (field, text)
    assert source_tags.decode_field(field, text) == ref


@pytest.mark.parametrize("ref", [SourceRef("musicbrainz", "artist", U), SourceRef("imdb", "name", "nm0000206"),
                                 SourceRef("theporndb", "performer", U), SourceRef("tmdb", "person", "6384")])
def test_a_reference_a_video_cannot_carry_is_not_encoded(ref):
    assert source_tags.encode_ref(ref) is None


@pytest.mark.parametrize("field,text", [
    ("imdb_id", "nm0000206"), ("imdb_id", "603"), ("tmdb_id", "603"), ("tmdb_id", "person:603"), ("tmdb_id", "movie:abc"),
    ("tmdb_id", ""), ("stashdb_id", "a-slug"), ("theporndb_id", "Not A Slug"), ("tvdb_id", "the-office"),
    ("source_url", "https://x"), ("nope", "tt1"),
])
def test_garbage_in_a_tag_decodes_to_nothing_and_never_raises(field, text):
    assert source_tags.decode_field(field, text) is None


def test_a_lookup_writes_one_field_per_service_the_link_and_the_match():
    values = source_tags.source_values(
        [SourceRef("tmdb", "episode", "1396/1/2"), SourceRef("imdb", "title", "tt0903747"), SourceRef("tmdb", "tv", "1396")],
        "title", today=DAY)
    assert values == {
        "tmdb_id": "episode:1396/1/2", "imdb_id": "tt0903747",  # the first reference of a service wins
        "source_url": "https://www.themoviedb.org/tv/1396/season/1/episode/2",
        "metadata_match": "tmdb;2026-10-16;title",
    }
    assert source_tags.source_values([], "title") == {}
    assert source_tags.source_values([SourceRef("musicbrainz", "artist", U)], "title") == {}


def test_the_service_page_is_used_as_the_link_only_when_it_names_the_same_record():
    ref = SourceRef("theporndb", "scene", U)
    same = source_tags.source_values([ref], "file", "https://theporndb.net/scenes/" + U, DAY)
    assert same["source_url"] == "https://theporndb.net/scenes/" + U
    other = source_tags.source_values([ref], "file", "https://evil.example/scenes/" + U, DAY)
    assert other["source_url"] == "https://theporndb.net/scenes/" + U  # an off-list page is never stored


def test_metadata_match_format():
    assert source_tags.make_match("stashdb", "file", DAY) == "stashdb;2026-10-16;file"
    assert source_tags.parse_match("tmdb;2026-10-16;manual") == ("tmdb", "2026-10-16", "manual")
    for bad in ("", "tmdb", "tmdb;2026-13-40;file", "tmdb;2026-10-16;guess", "TMDB;2026-10-16;file", "a;b;c;d", None):
        assert source_tags.parse_match(bad) is None
    with pytest.raises(ValueError):
        source_tags.make_match("tmdb", "guess")
    with pytest.raises(ValueError):
        source_tags.make_match("tm;db", "file")


def test_stage_sources_takes_only_valid_values():
    md = VideoMetadata()
    changed = source_tags.stage_sources(md, {
        "tmdb_id": "movie:603", "imdb_id": "not-an-id", "source_url": "https://evil.example/x",
        "metadata_match": "tmdb;2026-10-16;file", "title": "must not be touched here", "stashdb_id": "x"})
    assert changed and md.tmdb_id == "movie:603" and md.metadata_match == "tmdb;2026-10-16;file"
    assert md.imdb_id == "" and md.source_url == "" and md.stashdb_id == "" and md.title == ""
    assert not source_tags.stage_sources(md, {"tmdb_id": "movie:603"})  # nothing new


def test_scene_results_become_source_values():
    result = SceneResult(source="ThePornDB", external_id=U, page_url="https://theporndb.net/scenes/a-slug", kind="scene")
    values = lookup_sources.scene_values(result)
    assert values["theporndb_id"] == U and values["metadata_match"].endswith(";title")
    assert values["source_url"] == "https://theporndb.net/scenes/" + U  # the slug page names no verifiable record
    file_match = SceneResult(source="StashDB", external_id=U, match_kind="file match")
    assert lookup_sources.scene_values(file_match)["metadata_match"].startswith("stashdb;")
    assert lookup_sources.scene_values(file_match)["metadata_match"].endswith(";file")
    foreign = SceneResult(source="StashDB", external_id=U, id_trusted=False)
    assert lookup_sources.scene_values(foreign) == {}  # another stash-box's id is not a StashDB id
    assert lookup_sources.scene_values(SceneResult(source="StashDB", external_id="")) == {}
    assert lookup_sources.scene_values(SceneResult(source="ThePornDB", external_id="Not A Slug!")) == {}
    movie = SceneResult(source="ThePornDB", external_id=U, kind="movie")
    assert lookup_sources.scene_values(movie)["theporndb_id"] == "movie:" + U


def test_tmdb_values_include_the_ids_tmdb_itself_reports():
    values = lookup_sources.tmdb_values("movie", 603, imdb_id="tt0133093", how="title")
    assert values["tmdb_id"] == "movie:603" and values["imdb_id"] == "tt0133093"
    assert values["source_url"] == "https://www.themoviedb.org/movie/603"
    show = lookup_sources.tmdb_values("tv", 1396, 1, 2, imdb_id="tt0903747", tvdb_id=81189, how="manual")
    assert show["tmdb_id"] == "episode:1396/1/2" and show["tvdb_id"] == "series:81189"
    assert show["metadata_match"].endswith(";manual")
    assert lookup_sources.tmdb_values("tv", 1396)["tmdb_id"] == "tv:1396"
    assert lookup_sources.tvdb_values(73244)["tvdb_id"] == "series:73244"
    assert lookup_sources.imdb_values("tt0133093")["imdb_id"] == "tt0133093"
    assert lookup_sources.imdb_values("junk") == {}


def test_describe_builds_links_from_validated_ids_only():
    md = VideoMetadata(tmdb_id="movie:603", imdb_id="tt0133093", source_url="https://www.themoviedb.org/movie/603",
                       metadata_match="tmdb;2026-10-16;title", stashdb_id="garbage")
    rows = {label: (text, link) for label, text, link in source_tags.describe(md)}
    assert rows["TMDB ID"] == ("movie:603", "https://www.themoviedb.org/movie/603")
    assert rows["IMDb ID"][1] == "https://www.imdb.com/title/tt0133093/"
    assert rows["StashDB ID"] == ("garbage", "")  # shown as it is, never as a link
    assert "2026-10-16" in rows["Matched by"][0] and rows["Matched by"][1] == ""
    hostile = VideoMetadata(source_url="javascript:alert(1)")
    assert source_tags.describe(hostile) == [("Source link", "javascript:alert(1)", "")]
    assert source_tags.sources_summary(md) == "StashDB garbage, TMDB movie:603, IMDb tt0133093"
    assert source_tags.has_sources(md) and not source_tags.has_sources(VideoMetadata())


def test_ref_in_respects_kinds():
    md = VideoMetadata(tmdb_id="tv:1396")
    assert source_tags.ref_in(md, "tmdb", ("tv", "episode")).id == "1396"
    assert source_tags.ref_in(md, "tmdb", ("movie",)) is None
    assert source_tags.ref_in(md, "imdb") is None
    assert [r.service for r in source_tags.refs_in(VideoMetadata(imdb_id="tt1", tmdb_id="movie:2"))] == ["tmdb", "imdb"]


def test_settings_export_carries_no_per_file_source_data(tmp_path, monkeypatch):
    """Export/Import Settings must not include any source id, source link or poster (they are per-file data)."""
    import core.config as config
    from core import settings_adapter

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")
    text = repr([getattr(k, "key", k) for k in getattr(settings_adapter, "KEYS", [])]) + open(settings_adapter.__file__, encoding="utf-8").read()
    for word in ("THEPORNDB_ID", "STASHDB_ID", "TMDB_ID", "IMDB_ID", "TVDB_ID", "SOURCE_URL", "METADATA_MATCH", "cover_pending",
                 "theporndb_id", "source_url", "metadata_match", "cover_info"):
        assert word not in text, word
