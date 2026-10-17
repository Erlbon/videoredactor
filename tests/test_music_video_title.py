"""Music videos (owner's decision, option A): the Title is the SONG as MusicBrainz names it, version included; the
separate track title field is retired (old values are read and kept, never written, can be moved into an empty Title),
and MP4 music videos carry the iTunes media type stik = 6 next to our content-type tag."""

import json
import os
import shutil
import subprocess
import unittest.mock as mock
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

import core.config as config  # noqa: E402
from core import filename_pattern as fp  # noqa: E402
from core import mkv_backend as mkv  # noqa: E402
from core import mp4_backend as mp4b  # noqa: E402
from core import redact_steps as rs  # noqa: E402
from core import video_metadata as vm  # noqa: E402
from core.video_file import VideoFile  # noqa: E402
from core.video_metadata import ContentType, VideoMetadata  # noqa: E402
from tests.sample_clip import SAMPLE, SKIP_REASON  # noqa: E402
from tests.test_embedded_sources_mkv_fake_tools import FakeMkv  # noqa: E402
from redactor_common.cli import CliError  # noqa: E402
from videocli.main import main as cli_main  # noqa: E402

_app = QApplication.instance() or QApplication([])
OLD = "----:com.videoredactor:track_title"
CT = "----:com.videoredactor:content_type"
UNIT_SEP = chr(31)
needs_clip = pytest.mark.skipif(bool(SKIP_REASON), reason=SKIP_REASON or "sample clip")


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "videoredactor_settings.ini")


@pytest.fixture
def clip(tmp_path):
    path = tmp_path / "Depeche Mode - Enjoy the Silence.mp4"
    shutil.copy(SAMPLE, path)
    return path


def tag_file(path, **atoms):
    from mutagen.mp4 import MP4, MP4FreeForm

    f = MP4(str(path))
    if f.tags is None:
        f.add_tags()
    for key, value in atoms.items():
        f.tags[key] = [MP4FreeForm(value.encode())] if key.startswith("----") else value
    f.save()


def tags_of(path):
    from mutagen.mp4 import MP4

    return MP4(str(path)).tags or {}


def loaded(path):
    v = VideoFile(path=Path(path))
    v.load()
    assert not v.load_error
    return v


# --- 1. the track title field is retired ----------------------------------------------------------------------


def test_track_title_is_gone_from_every_field_list():
    from gui import main_window as mw
    from gui.tag_panel import FIELD_LABELS, TagPanel

    assert not hasattr(VideoMetadata(), "track_title")
    placeholders = [f for f, _ in mw.FILENAME_PLACEHOLDERS]
    for names in (vm.EDITABLE_FIELDS, vm.TEXT_FIELDS, vm.UNIVERSAL_FIELDS, *vm.TYPE_SPECIFIC_FIELDS.values(),
                  FIELD_LABELS, mw.FILTER_COLUMN_KINDS, mw.COLUMN_LABEL_LOOKUP, placeholders,
                  mkv.FIELD_TO_MKV_TAG, mp4b.CUSTOM_FIELDS, fp.VALID_FIELD_KEYS):
        assert "track_title" not in names
    panel = TagPanel()
    assert "track_title" not in panel._inputs


@needs_clip
def test_mp4_old_track_title_is_read_and_kept_but_never_written(clip):
    tag_file(clip, **{OLD: "Old Track"})
    v = loaded(clip)
    assert v.metadata.legacy_track_title == "Old Track"
    v.metadata.title = "Enjoy the Silence (Live in Barcelona)"
    v.metadata.artist = "Depeche Mode"
    v.dirty = True
    v.save()
    assert v.save_error == ""
    assert bytes(tags_of(clip)[OLD][0]) == b"Old Track"  # kept exactly, not rewritten, not deleted
    assert loaded(clip).metadata.legacy_track_title == "Old Track"


@needs_clip
def test_mp4_never_creates_a_track_title_atom(clip):
    v = loaded(clip)
    v.metadata.title = "Personal Jesus (Stripped)"
    v.metadata.content_type = ContentType.MUSIC_VIDEO
    v.dirty = True
    v.save()
    assert v.save_error == "" and OLD not in tags_of(clip)


def test_mkv_old_track_title_is_read_and_carried_over_untouched():
    fake = FakeMkv(tags_xml="<Tags><Tag><Simple><Name>TRACK_TITLE</Name><String>Old Track</String></Simple></Tag></Tags>")
    with mock.patch("subprocess.run", side_effect=fake.run), mock.patch("core.video_file.is_tool_available", return_value=True):
        v = VideoFile(path=Path("/fake/Clip.mkv"), metadata=VideoMetadata())
        v.load()
        assert v.metadata.legacy_track_title == "Old Track"
        v.metadata.title = "Enjoy the Silence"
        v.metadata.content_type = ContentType.MUSIC_VIDEO
        v.dirty = True
        v.save()
        assert v.save_error == ""
    tags = mkv.parse_tags_xml(fake.tags_xml)
    assert tags["TRACK_TITLE"] == "Old Track"  # carried over like another tool's tag
    assert tags["VIDEOREDACTOR_CONTENT_TYPE"] == "Music Video"


def test_mkv_new_file_gets_no_track_title_tag():
    fake = FakeMkv()
    with mock.patch("subprocess.run", side_effect=fake.run), mock.patch("core.video_file.is_tool_available", return_value=True):
        v = VideoFile(path=Path("/fake/Clip.mkv"), metadata=VideoMetadata(title="Song", artist="A"))
        v.dirty = True
        v.save()
    assert "TRACK_TITLE" not in mkv.parse_tags_xml(fake.tags_xml)


def test_move_track_title_rules():
    mv = VideoMetadata(content_type=ContentType.MUSIC_VIDEO, legacy_track_title="Enjoy the Silence")
    assert vm.can_move_track_title(mv) and vm.move_track_title(mv) and mv.title == "Enjoy the Silence"
    both = VideoMetadata(content_type=ContentType.MUSIC_VIDEO, title="Song A", legacy_track_title="Song B")
    assert not vm.can_move_track_title(both) and not vm.move_track_title(both) and both.title == "Song A"
    assert vm.old_track_title_to_show(both) == "Song B"  # a differing old value stays visible, read-only
    same = VideoMetadata(title="X", legacy_track_title="X")
    assert vm.old_track_title_to_show(same) == ""
    movie = VideoMetadata(content_type=ContentType.MOVIE, legacy_track_title="X")
    assert not vm.can_move_track_title(movie)
    nothing = VideoMetadata(content_type=ContentType.MUSIC_VIDEO)
    assert not vm.can_move_track_title(nothing)


class _Ctx:
    def __init__(self, meta):
        self.work = type("W", (), {"metadata": meta, "dirty": False})()


def test_redact_step_is_off_by_default_suggestion_only_and_applies_the_move():
    step = rs.TrackTitleStep()
    assert step.default_enabled is False
    assert step.key in [s.key for s in rs.build_catalogue()]
    ctx = _Ctx(VideoMetadata(content_type=ContentType.MUSIC_VIDEO, legacy_track_title="Enjoy the Silence"))
    result = step.execute(ctx)
    assert result.status.value == "suggestion" and result.review_only
    assert ctx.work.metadata.title == ""  # a suggestion changes nothing by itself
    lines = step.apply_suggestion(ctx, result)
    assert ctx.work.metadata.title == "Enjoy the Silence" and ctx.work.dirty and lines
    differ = _Ctx(VideoMetadata(content_type=ContentType.MUSIC_VIDEO, title="A", legacy_track_title="B"))
    assert step.execute(differ).status.value == "nothing"
    assert differ.work.metadata.title == "A"


# --- old settings load without error ----------------------------------------------------------------------------


def test_old_settings_mentioning_track_title_load_and_drop_it(monkeypatch):
    import gui.main_window as mw

    config.set_setting("table", "column_order", "filename,status,artist,track_title,album,title")
    config.set_setting("table", "hidden_columns", "track_title,comment")
    config.set_setting("table", "column_widths", json.dumps({"track_title": 120, "title": 200}))
    config.set_setting("filename_patterns", "history", UNIT_SEP.join(["%artist% - %track_title%", "%show_title% - %title%"]))
    recipe = {"order": ["filename_tags", "rename"], "enabled": {"filename_tags": True, "rename": True},
              "options": {"filename_tags": {"pattern": "%artist% - %track_title%"},
                          "rename": {"pattern": "%artist% - %track_title%"}}}
    config.set_setting("redact", "recipe", json.dumps(recipe))
    monkeypatch.setattr(mw.MainWindow, "_restore_last_folder_on_startup", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_check_external_tools_on_startup", lambda self: None)
    w = mw.MainWindow()
    try:
        assert "track_title" not in w._column_order and "title" in w._column_order
        assert "track_title" not in w.tag_panel._inputs
    finally:
        w.close()
    history = fp.load_pattern_history()
    assert history[0] == "%artist% - %title%" and "track_title" not in " ".join(history)
    loaded_recipe = rs.load_recipe(rs.build_catalogue())
    assert loaded_recipe.options["filename_tags"]["pattern"] == "%artist% - %title%"
    assert loaded_recipe.options["rename"]["pattern"] == "%artist% - %title%"


def test_old_patterns_still_parse_and_render():
    assert fp.parse_filename("Depeche Mode - Enjoy the Silence", "%artist% - %track_title%") == {
        "artist": "Depeche Mode", "title": "Enjoy the Silence"}
    meta = VideoMetadata(artist="Depeche Mode", title="Enjoy the Silence")
    assert fp.render_filename(meta, "%artist% - %track_title%") == "Depeche Mode - Enjoy the Silence"
    # a pattern that already has %title% keeps working, the retired token renders as nothing
    assert fp.render_filename(meta, "%artist% - %title% %track_title%").startswith("Depeche Mode - Enjoy the Silence")


def test_parse_filename_default_for_music_videos():
    mv = [VideoFile(path=Path("a.mp4"), metadata=VideoMetadata(content_type=ContentType.MUSIC_VIDEO))]
    tv = [VideoFile(path=Path("b.mp4"), metadata=VideoMetadata(content_type=ContentType.TV))]
    assert fp.default_parse_pattern(mv) == "%artist% - %title%"
    assert fp.default_parse_pattern(mv + tv) == fp.DEFAULT_RENAME_PATTERN
    assert fp.default_parse_pattern([]) == fp.DEFAULT_RENAME_PATTERN


# --- the panel and the main window --------------------------------------------------------------------------------


@pytest.fixture
def window(monkeypatch):
    import gui.main_window as mw

    monkeypatch.setattr(mw.MainWindow, "_restore_last_folder_on_startup", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_check_external_tools_on_startup", lambda self: None)
    w = mw.MainWindow()
    yield w
    for vf in w.video_files:
        vf.dirty = False
    w.close()


def _select_first(w, files):
    w.video_files = files
    w._refresh_table_rows()
    w.table.selectRow(0)
    w._on_selection_changed()


def _video(tmp_path, **meta):
    p = tmp_path / "a.mp4"
    p.write_bytes(b"")
    return VideoFile(path=p, metadata=VideoMetadata(**meta))


def test_panel_shows_old_track_title_and_the_move_action(window, tmp_path):
    vf = _video(tmp_path, content_type=ContentType.MUSIC_VIDEO, artist="DM", legacy_track_title="Enjoy the Silence")
    _select_first(window, [vf])
    panel = window.tag_panel
    assert not panel.old_track_title_label.isHidden() and "Enjoy the Silence" in panel.old_track_title_label.text()
    assert not panel.move_track_title_button.isHidden()
    panel.move_track_title_button.click()
    assert vf.metadata.title == "Enjoy the Silence" and vf.dirty
    assert panel.move_track_title_button.isHidden()  # nothing left to move
    window.undo_last_action()
    assert vf.metadata.title == "" and not vf.dirty


def test_panel_keeps_a_differing_old_value_read_only_and_offers_no_move(window, tmp_path):
    vf = _video(tmp_path, content_type=ContentType.MUSIC_VIDEO, title="Song A", legacy_track_title="Song B")
    _select_first(window, [vf])
    panel = window.tag_panel
    assert "Song B" in panel.old_track_title_label.text() and not panel.old_track_title_label.isHidden()
    assert panel.move_track_title_button.isHidden()
    assert vf.metadata.title == "Song A" and not vf.dirty


def test_panel_flags_a_content_type_that_came_from_the_media_type(window, tmp_path):
    vf = _video(tmp_path, content_type=ContentType.MUSIC_VIDEO, content_type_from_media_type=True)
    _select_first(window, [vf])
    assert not window.tag_panel.media_type_note.isHidden()
    window._on_apply_to_selected({"content_type": "Music Video"})  # the user's own choice now
    assert window.tag_panel.media_type_note.isHidden() and vf.metadata.content_type_from_media_type is False


# --- 2. stik in MP4 --------------------------------------------------------------------------------------------------


def ffprobe_tags(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out).get("format", {}).get("tags", {})


@needs_clip
def test_music_video_writes_stik_6_with_the_content_type_tag(clip):
    v = loaded(clip)
    v.metadata.content_type = ContentType.MUSIC_VIDEO
    v.metadata.title = "Enjoy the Silence"
    v.dirty = True
    v.save()
    assert v.save_error == ""
    tags = tags_of(clip)
    assert list(tags["stik"]) == [6] and bytes(tags[CT][0]) == b"Music Video"
    assert ffprobe_tags(clip).get("media_type") == "6"  # ffprobe reads the same atom
    again = loaded(clip)
    assert again.metadata.media_type == 6 and again.metadata.content_type is ContentType.MUSIC_VIDEO
    assert again.metadata.content_type_from_media_type is False  # its own content type tag exists


@needs_clip
def test_other_content_types_never_touch_stik(clip):
    v = loaded(clip)
    v.metadata.content_type = ContentType.MOVIE
    v.metadata.title = "A Film"
    v.dirty = True
    v.save()
    assert "stik" not in tags_of(clip)
    tag_file(clip, stik=[9])
    v = loaded(clip)
    assert v.metadata.media_type == 9 and v.metadata.content_type is ContentType.MOVIE
    v.metadata.title = "Renamed"
    v.dirty = True
    v.save()
    assert list(tags_of(clip)["stik"]) == [9]


@needs_clip
def test_an_existing_different_stik_is_not_overwritten_by_music_video(clip):
    tag_file(clip, stik=[10])
    v = loaded(clip)
    v.metadata.content_type = ContentType.MUSIC_VIDEO
    v.metadata.title = "Song"
    v.dirty = True
    v.save()
    assert v.save_error == "" and list(tags_of(clip)["stik"]) == [10]
    assert loaded(clip).metadata.content_type is ContentType.MUSIC_VIDEO  # our own tag still says so


@needs_clip
def test_stik_6_without_a_content_type_reads_as_music_video_flagged_and_unsaved(clip):
    tag_file(clip, stik=[6])
    v = loaded(clip)
    assert v.metadata.content_type is ContentType.MUSIC_VIDEO and v.metadata.content_type_from_media_type
    assert not v.dirty
    assert CT not in tags_of(clip)  # a suggestion: the file is not touched on load


@needs_clip
def test_stik_6_does_not_override_a_content_type_the_file_has(clip):
    tag_file(clip, stik=[6], **{CT: "Clip"})
    v = loaded(clip)
    assert v.metadata.content_type is ContentType.CLIP and not v.metadata.content_type_from_media_type


@needs_clip
def test_clearing_the_content_type_of_a_stik_file_saves_without_a_false_error(clip):
    tag_file(clip, stik=[6])
    v = loaded(clip)
    v.metadata.content_type = ContentType.UNSET
    v.metadata.title = "Song"
    v.dirty = True
    v.save()
    assert v.save_error == ""
    assert list(tags_of(clip)["stik"]) == [6]  # the media type is not ours to remove


def test_mkv_has_no_media_type_only_the_custom_tag():
    fake = FakeMkv()
    with mock.patch("subprocess.run", side_effect=fake.run), mock.patch("core.video_file.is_tool_available", return_value=True):
        v = VideoFile(path=Path("/fake/Clip.mkv"), metadata=VideoMetadata(title="Song", content_type=ContentType.MUSIC_VIDEO))
        v.dirty = True
        v.save()
        assert v.save_error == ""
        assert v.metadata.media_type is None
    assert mkv.parse_tags_xml(fake.tags_xml)["VIDEOREDACTOR_CONTENT_TYPE"] == "Music Video"


# --- command line ---------------------------------------------------------------------------------------------------


def cli(capsys, *argv):
    try:
        code = cli_main(list(argv))
    except SystemExit as e:
        code = e.code
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@needs_clip
def test_cli_track_title_is_an_alias_of_title_with_a_deprecation_note(clip, capsys):
    tag_file(clip, **{OLD: "Old Track"})
    code, out, err = cli(capsys, "set", str(clip), "-s", "track_title=Enjoy the Silence")
    assert code == 0, out + err
    v = loaded(clip)
    assert v.metadata.title == "Enjoy the Silence" and bytes(tags_of(clip)[OLD][0]) == b"Old Track"
    code, out, err = cli(capsys, "set", "--help")
    assert "track_title is deprecated" in " ".join(out.split())
    code, out, _ = cli(capsys, "info", "--help")
    assert "track_title is deprecated" in " ".join(out.split())


@needs_clip
def test_cli_info_shows_the_old_value_and_set_refuses_it(clip, capsys):
    tag_file(clip, **{OLD: "Old Track"})
    code, out, _ = cli(capsys, "info", str(clip), "--fields", "legacy_track_title")
    assert code == 0 and "Old Track" in out
    with pytest.raises(CliError, match="legacy_track_title is read-only"):
        cli(capsys, "set", str(clip), "-s", "legacy_track_title=Hack")
    assert bytes(tags_of(clip)[OLD][0]) == b"Old Track"
