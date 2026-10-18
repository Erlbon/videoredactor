"""Porn files: Studio (the site), Network (its parent), Collection (the group), Scene # (the episode number), Tags
(their own pick-list), and the Website derived from the Studio through the Studios table."""

from core import controlled_vocab, studios
from core.video_metadata import PANEL_LABELS_BY_TYPE, ContentType, VideoMetadata, fields_for_content_type
from tests.test_clear_fields import select
from tests.test_per_file_confirmation import close, make_window


def test_the_porn_type_has_scene_network_and_collection_fields():
    fields = fields_for_content_type(ContentType.PORN)
    assert {"studio", "network", "collection", "episode_number"} <= set(fields)
    assert "season_number" not in fields and "show_title" not in fields
    assert PANEL_LABELS_BY_TYPE[ContentType.PORN]["episode_number"] == "Scene #"


def test_websites_are_stored_as_bare_hosts_and_matched_by_studio():
    assert studios.normalize_website("https://www.EvilAngel.com/en/video/x") == "evilangel.com"
    assert studios.normalize_website("site-name.com") == "site-name.com"
    assert studios.normalize_website("not a site") == "" and studios.normalize_website("") == ""
    table = {"Evil Angel": "evilangel.com"}
    assert studios.website_for("evil  angel", table) == "evilangel.com"
    assert studios.website_for("Other, Evil Angel", table) == "evilangel.com"
    assert studios.website_for("Unknown", table) == ""


def test_a_lookup_adds_a_website_but_never_overwrites_one(monkeypatch, tmp_path):
    import core.config as config

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")
    assert studios.remember_from_lookup("Evil Angel", ["https://www.evilangel.com/en/video/1"]) is True
    assert studios.website_for("Evil Angel") == "evilangel.com"
    assert studios.remember_from_lookup("Evil Angel", ["https://other.example/x"]) is False  # the entry wins
    assert studios.website_for("Evil Angel") == "evilangel.com"
    assert studios.remember_from_lookup("", ["https://a.example"]) is False
    assert studios.remember_from_lookup("No Page", []) is False


def test_the_porn_tag_list_grows_from_lookups_and_is_separate_from_the_genres(monkeypatch, tmp_path):
    import core.config as config

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")
    assert controlled_vocab.get_porn_tag_options() == []
    assert controlled_vocab.remember_porn_tags("Tag B, Tag A") == 2
    assert controlled_vocab.remember_porn_tags("Tag A, Tag C") == 1
    assert controlled_vocab.get_porn_tag_options() == ["Tag A", "Tag B", "Tag C"] or \
        set(controlled_vocab.get_porn_tag_options()) == {"Tag A", "Tag B", "Tag C"}
    assert "Action" not in controlled_vocab.get_porn_tag_options()
    controlled_vocab.remove_porn_tag_option("Tag B")
    assert "Tag B" not in controlled_vocab.get_porn_tag_options()


def test_the_panel_shows_scene_and_tags_for_a_porn_file(monkeypatch, tmp_path):
    w = make_window(monkeypatch, tmp_path, ["a.mp4"])
    vf = w.video_files[0]
    vf.metadata.content_type = ContentType.PORN
    vf.metadata.genre_tags = "Tag X, Tag Y"
    vf.metadata.episode_number = 2
    vf.metadata.studio = "Evil Angel"
    select(w, vf)
    labels = {key: label.text() for key, label in w.tag_panel._labels.items()}
    assert labels["episode_number"] == "Scene #" and labels["genre_tags"] == "Tags"
    assert w.tag_panel._inputs["episode_number"].value() == 2
    assert w.tag_panel._inputs["genre_tags"].checked_items() == ["Tag X", "Tag Y"]  # kept although not in any list
    close(w)


def test_the_website_column_and_panel_line_follow_the_studio(monkeypatch, tmp_path):
    w = make_window(monkeypatch, tmp_path, ["a.mp4", "b.mp4"])
    studios.save_studios({"Evil Angel": "evilangel.com"})
    a, b = w.video_files
    a.metadata.studio, b.metadata.studio = "Evil Angel", "Elsewhere"
    assert w._display_value(a, "website") == "evilangel.com" and w._display_value(b, "website") == ""
    select(w, a)
    assert not w.tag_panel.website_label.isHidden() and "evilangel.com" in w.tag_panel.website_label.text()
    select(w, b)
    assert w.tag_panel.website_label.isHidden()
    close(w)


def test_a_movie_keeps_the_movie_genre_list_and_label(monkeypatch, tmp_path):
    w = make_window(monkeypatch, tmp_path, ["a.mp4"])
    vf = w.video_files[0]
    vf.metadata.content_type = ContentType.MOVIE
    select(w, vf)
    assert w.tag_panel._labels["genre_tags"].text() == "Genre / Tags"
    assert "Action" in w.tag_panel._inputs["genre_tags"].options
    close(w)


def test_a_lookup_fills_the_scene_number_from_the_file_name_when_the_file_has_none(tmp_path):
    from core.scene_lookup import SceneResult
    from core.video_file import VideoFile
    from gui.scene_lookup_flow import fields_for

    result = SceneResult(source="X", external_id="1", title="Anal Oil Latex", studio="Evil Angel",
                         parent_studio="Evil Angel Network")
    path = tmp_path / "AnalOilLatex_s02.mp4"
    path.write_bytes(b"x")
    vf = VideoFile(path=path, metadata=VideoMetadata())
    fields = fields_for(vf, result)
    assert fields["episode_number"] == "2" and fields["network"] == "Evil Angel Network" and "collection" not in fields
    vf.metadata.episode_number = 5  # a number the file already has is never replaced
    assert "episode_number" not in fields_for(vf, result)
