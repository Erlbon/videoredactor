"""Clear Fields: Edit > Clear Fields..., the side panel's Clear All Fields button, the right-click entry and
`videoredactor clear`. Real MainWindow offscreen, real generated MP4s (mutagen and ffprobe read back), the in-memory
MKVToolNix model (NOT proven against the real tool). A cleared file must have the atoms/tags REMOVED (not written empty),
no poster, and still probe; stik is left alone; everything is staged and undoable until Save."""

import json
import os
import shutil
import subprocess

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog  # noqa: E402
from redactor_common.gui.clear_fields_dialog import ClearFieldsDialog  # noqa: E402

import gui.main_window as mw  # noqa: E402
from core import clear_plan, mkv_backend as mkv  # noqa: E402
from core.poster import prepare_poster  # noqa: E402
from core.video_file import VideoFile  # noqa: E402
from core.video_metadata import ContentType, VideoMetadata  # noqa: E402
from tests.sample_clip import SAMPLE, SKIP_REASON  # noqa: E402
from tests.test_embedded_sources_mkv_fake_tools import JPEG, make as make_mkv, mkv_file  # noqa: E402,F401
from tests.test_per_file_confirmation import close, make_window, state  # noqa: E402
from tests.test_poster import solid  # noqa: E402

_app = QApplication.instance() or QApplication([])
needs_ffmpeg = pytest.mark.skipif(bool(SKIP_REASON), reason=SKIP_REASON or "sample clip")


def fill(vf, with_poster=True):
    md = vf.metadata
    md.content_type = ContentType.MUSIC_VIDEO
    md.title, md.artist, md.album, md.composer, md.lyrics = "Song", "Band", "Album", "Comp", "la la\n\nla"
    md.description, md.genre_tags, md.release_date, md.language = "Plot", "Rock", "1990", "English"
    md.show_title, md.season_number, md.episode_number, md.network = "Show", 2, 5, "Net"
    md.tmdb_id, md.imdb_id, md.tvdb_id = "movie:603", "tt0133093", "series:81189"
    md.source_url, md.metadata_match = "https://www.themoviedb.org/movie/603", "tmdb;2026-10-17;title"
    if with_poster:
        md.cover_pending = prepare_poster(solid(600, 900, "PNG")).jpeg
    vf.dirty = True


def accept_dialog(monkeypatch, groups=None, seen=None):
    """Clicks Clear in the real ClearFieldsDialog (with `groups` ticked, default as the dialog starts)."""
    def fake_exec(self):
        if seen is not None:
            seen.append(self)
        if groups is not None:
            for key, box in self.boxes.items():
                box.setChecked(key in groups)
        self.clear_button.click()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(ClearFieldsDialog, "exec", fake_exec)


def select(window, *files):
    window.table.clearSelection()
    sm = window.table.selectionModel()
    for row in range(window.table.rowCount()):
        if window._file_for_row(row) in files:
            sm.select(window.table.model().index(row, 0), sm.SelectionFlag.Select | sm.SelectionFlag.Rows)
    window._on_selection_changed()


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format_tags:stream=index,codec_type:stream_disposition=attached_pic",
         "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


# -- the plan and the staged clearing (pure) ----------------------------------------------------------------------------

def test_the_groups_and_what_is_never_in_them():
    assert clear_plan.DEFAULT_GROUP_KEYS == ["descriptive", "sources", "poster"]  # the content type is off by default
    assert not [g for g in clear_plan.GROUPS if g.key == "type"][0].default_checked
    everything = {f for fields in clear_plan.GROUP_FIELDS.values() for f in fields}
    for never in ("scan_stamp", "legacy_track_title", "duration_seconds", "resolution", "media_type", "container"):
        assert never not in everything
    assert {"title", "lyrics", "composer", "show_title", "season_number"} <= set(clear_plan.GROUP_FIELDS["descriptive"])
    assert "content_type" not in clear_plan.GROUP_FIELDS["descriptive"]


def test_stage_clear_leaves_the_scan_stamp_technical_fields_and_legacy_title(tmp_path):
    path = tmp_path / "A.mp4"
    path.write_bytes(b"x")
    vf = VideoFile(path=path, metadata=VideoMetadata(scan_stamp="OK;2026;abc", resolution="1920x1080", bitrate="5000",
                                                     legacy_track_title="Old", media_type=6, container="mp4"))
    fill(vf, with_poster=False)
    assert clear_plan.stage_clear(vf, clear_plan.DEFAULT_GROUP_KEYS)
    md = vf.metadata
    assert (md.scan_stamp, md.resolution, md.bitrate, md.legacy_track_title, md.media_type) == ("OK;2026;abc", "1920x1080", "5000", "Old", 6)
    assert md.title == md.lyrics == md.tmdb_id == "" and md.season_number is None and md.episode_number is None
    assert md.content_type is ContentType.MUSIC_VIDEO  # the type is only cleared when asked
    assert clear_plan.stage_clear(vf, ["type"]) and md.content_type is ContentType.UNSET
    assert not clear_plan.stage_clear(vf, clear_plan.ALL_GROUP_KEYS)  # nothing left to clear


# -- the window: real MP4s -----------------------------------------------------------------------------------------------

NAMES = ["One.mp4", "Two.mp4", "Three.mp4", "Four.mp4"]


@needs_ffmpeg
def test_a_fully_tagged_mp4_is_blank_after_clear_and_save_and_still_probes(monkeypatch, tmp_path):
    w = make_window(monkeypatch, tmp_path, NAMES[:1], real=True)
    vf = w.video_files[0]
    fill(vf)
    w._on_save_all()
    assert not vf.save_error
    from mutagen.mp4 import MP4
    tags = MP4(str(vf.path)).tags
    assert "\xa9nam" in tags and "covr" in tags and "\xa9lyr" in tags and "----:com.videoredactor:TMDB_ID" in tags
    assert tags["stik"] == [6]
    assert any(s["disposition"]["attached_pic"] for s in probe(vf.path)["streams"])
    select(w, vf)
    accept_dialog(monkeypatch)
    w._on_clear_fields()
    assert vf.dirty and vf.metadata.title == "" and vf.metadata.cover_remove
    w._on_save_all()
    assert not vf.save_error, vf.save_error
    tags = MP4(str(vf.path)).tags or {}
    left = sorted(k for k in tags if k not in ("stik", "\xa9too"))
    assert left == ["----:com.videoredactor:content_type"], left  # no title, ARTIST, ids, lyrics, covr; the type stays
    assert tags.get("stik") == [6]  # the media type atom is left alone
    info = probe(vf.path)
    assert not any(s["disposition"]["attached_pic"] for s in info["streams"])  # the cover is gone
    assert {s["codec_type"] for s in info["streams"]} == {"video", "audio"}  # still a playable file
    again = VideoFile(path=vf.path)
    again.load()
    assert not again.load_error and again.metadata.title == "" and again.metadata.tmdb_id == ""
    assert again.metadata.cover_info == "" and again.metadata.lyrics == ""
    assert again.metadata.content_type is ContentType.MUSIC_VIDEO
    close(w)


@needs_ffmpeg
def test_the_regression_story_four_files_one_wrongly_tagged_is_returned_to_blank(monkeypatch, tmp_path):
    """The TV import bug put show data, a poster and a TMDB id on a file that did not belong; the owner must be able to
    restore it to blank without touching the other three."""
    w = make_window(monkeypatch, tmp_path, NAMES, real=True)
    files = w.video_files
    for vf in files[:3]:
        vf.metadata.title, vf.metadata.show_title, vf.metadata.tmdb_id = f"Right {vf.path.stem}", "Show", "episode:1/1/1"
        vf.metadata.cover_pending = prepare_poster(solid(600, 900, "PNG")).jpeg
        vf.dirty = True
    wrong = files[3]  # tagged directly, the way the old import did
    wrong.metadata.title, wrong.metadata.show_title, wrong.metadata.network = "Wrong", "Other Show", "AMC"
    wrong.metadata.tmdb_id, wrong.metadata.tvdb_id = "tv:999", "series:5"
    wrong.metadata.cover_pending = prepare_poster(solid(600, 900, "PNG")).jpeg
    wrong.dirty = True
    w._on_save_all()
    others_before = [state(vf) for vf in files[:3]]
    select(w, wrong)
    accept_dialog(monkeypatch)
    w._on_clear_fields()
    w._on_save_all()
    from mutagen.mp4 import MP4
    assert not (MP4(str(wrong.path)).tags or {}).get("tvsh") and "covr" not in (MP4(str(wrong.path)).tags or {})
    reread = VideoFile(path=wrong.path)
    reread.load()
    assert (reread.metadata.title, reread.metadata.show_title, reread.metadata.tmdb_id, reread.metadata.cover_info) == ("", "", "", "")
    assert [state(vf) for vf in files[:3]] == others_before
    for vf in files[:3]:
        tags = MP4(str(vf.path)).tags
        assert tags["\xa9nam"] == [f"Right {vf.path.stem}"] and "covr" in tags
    close(w)


def test_undo_before_save_restores_values_ids_and_the_poster_state_exactly(monkeypatch, tmp_path):
    w = make_window(monkeypatch, tmp_path, NAMES[:2])
    first, second = w.video_files
    fill(first)
    first.metadata.cover_info = "JPEG 600x900, 80 KB"  # as if it was saved with a poster
    fill(second, with_poster=False)
    before = [state(vf) for vf in w.video_files]
    select(w, first, second)
    accept_dialog(monkeypatch)
    w._on_clear_fields()
    assert all(vf.dirty and vf.metadata.title == "" for vf in w.video_files)
    assert first.metadata.cover_remove and first.metadata.cover_pending is None
    assert "Cleared fields of 2 file(s)" in w.status_bar.currentMessage()
    w.undo_last_action()
    assert [state(vf) for vf in w.video_files] == before
    assert not first.metadata.cover_remove and first.metadata.cover_pending is not None
    close(w)


def test_only_the_selected_files_are_cleared_and_the_others_untouched(monkeypatch, tmp_path):
    w = make_window(monkeypatch, tmp_path, NAMES)
    for vf in w.video_files:
        fill(vf, with_poster=False)
    before = [state(vf) for vf in w.video_files]
    select(w, w.video_files[0], w.video_files[2])
    accept_dialog(monkeypatch)
    w._on_clear_fields()
    assert w.video_files[0].metadata.title == "" and w.video_files[2].metadata.title == ""
    assert state(w.video_files[1]) == before[1] and state(w.video_files[3]) == before[3]
    close(w)


def test_nothing_selected_says_so_and_changes_nothing(monkeypatch, tmp_path):
    w = make_window(monkeypatch, tmp_path, NAMES[:1])
    fill(w.video_files[0], with_poster=False)
    w.table.clearSelection()
    w._on_selection_changed()
    before = state(w.video_files[0])
    monkeypatch.setattr(ClearFieldsDialog, "exec", lambda self: pytest.fail("the dialog opened"))
    w._on_clear_fields()
    assert "select at least one file" in w.status_bar.currentMessage() and state(w.video_files[0]) == before
    close(w)


def test_the_dialog_counts_the_groups_and_cancel_is_the_default_button(monkeypatch, tmp_path):
    w = make_window(monkeypatch, tmp_path, NAMES[:3])
    fill(w.video_files[0])
    w.video_files[0].metadata.cover_info = "JPEG 600x900, 80 KB"
    w.video_files[1].metadata.title = "Only a title"
    seen = []
    select(w, *w.video_files)

    def fake_exec(self):
        seen.append(self)
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(ClearFieldsDialog, "exec", fake_exec)
    before = [state(vf) for vf in w.video_files]
    w._on_clear_fields()
    d = seen[0]
    assert d.windowTitle() == "Clear fields of 3 file(s)"
    assert d.boxes["descriptive"].text().endswith(": 14 values in 2 files")
    assert d.boxes["sources"].text().endswith(": 5 values in 1 file")
    assert "1 value in 1 file" in d.boxes["poster"].text() and "1 value in 1 file" in d.boxes["type"].text()
    assert d.boxes["descriptive"].isChecked() and d.boxes["sources"].isChecked() and d.boxes["poster"].isChecked()
    assert not d.boxes["type"].isChecked()
    assert d.cancel_button.isDefault() and not d.clear_button.isDefault()
    assert "empties the fields in the file(s) when you Save" in d.warning_label.text()
    assert [state(vf) for vf in w.video_files] == before  # cancelled: nothing changed
    assert "cancelled" in w.status_bar.currentMessage()
    close(w)


def test_the_content_type_is_cleared_only_when_ticked(monkeypatch, tmp_path):
    w = make_window(monkeypatch, tmp_path, NAMES[:1])
    vf = w.video_files[0]
    fill(vf, with_poster=False)
    select(w, vf)
    accept_dialog(monkeypatch)
    w._on_clear_fields()
    assert vf.metadata.content_type is ContentType.MUSIC_VIDEO
    accept_dialog(monkeypatch, groups=["type"])
    w._on_clear_fields()
    assert vf.metadata.content_type is ContentType.UNSET
    close(w)


def test_the_menu_the_panel_button_and_the_right_click_menu_all_open_it(monkeypatch, tmp_path):
    w = make_window(monkeypatch, tmp_path, NAMES[:1])
    calls = []
    monkeypatch.setattr(mw.MainWindow, "_on_clear_fields", lambda self: calls.append("x"))
    # the Edit menu entry and its shortcut-free action
    assert w.actions_by_key["clear_fields"].text().replace("&", "") == "Clear Fields…"
    assert w.actions_by_key["clear_fields"].shortcut().isEmpty()
    w.tag_panel.clear_fields_requested.disconnect()
    w.tag_panel.clear_fields_requested.connect(lambda: calls.append("panel"))
    w.tag_panel.clear_fields_button.click()
    assert calls == ["panel"] and w.tag_panel.clear_fields_button.text() == "Clear All Fields..."
    items = w._table_context_extra_items([w.video_files[0]])
    assert any(getattr(i, "key", "") == "ctx_clear_fields" for i in items)
    close(w)


def test_the_panel_refreshes_after_a_clear(monkeypatch, tmp_path):
    w = make_window(monkeypatch, tmp_path, NAMES[:1])
    vf = w.video_files[0]
    fill(vf, with_poster=False)
    select(w, vf)
    w.tag_panel.set_content_type_filter(ContentType.MUSIC_VIDEO)
    w._on_selection_changed()
    assert w.tag_panel._inputs["title"].text() == "Song"
    accept_dialog(monkeypatch)
    w._on_clear_fields()
    assert w.tag_panel._inputs["title"].text() == ""
    close(w)


# -- MKV through the fake-tools model --------------------------------------------------------------------------------------

def test_mkv_clear_removes_the_tags_and_deletes_the_cover_by_id(mkv_file):
    mkv_file.attachments = [{"file_name": "cover.jpg", "content_type": "image/jpeg", "data": JPEG},
                            {"file_name": "font.ttf", "content_type": "font/ttf", "data": b"font"}]
    mkv_file.tags_xml = ("<Tags><Tag><Simple><Name>ENCODER_SETTINGS</Name><String>x264</String></Simple></Tag></Tags>")
    vf = make_mkv(VideoMetadata(title="Movie"))
    fill(vf, with_poster=False)
    vf.metadata.cover_info = "JPEG, 1 KB"
    vf.dirty = True
    vf.save()
    assert vf.save_error == "" and "LYRICS" in mkv.parse_tags_xml(mkv_file.tags_xml)
    clear_plan.stage_clear(vf, clear_plan.DEFAULT_GROUP_KEYS)
    vf.save()
    assert vf.save_error == "", vf.save_error
    tags = mkv.parse_tags_xml(mkv_file.tags_xml)
    assert set(tags) <= {"ENCODER_SETTINGS", "VIDEOREDACTOR_CONTENT_TYPE"} and tags["ENCODER_SETTINGS"] == "x264"  # others' tags kept
    assert "TMDB_ID" not in tags and "LYRICS" not in tags and "ARTIST" not in tags
    assert [a["file_name"] for a in mkv_file.attachments] == ["font.ttf"]  # the cover went, the font stayed
    assert mkv_file.propedit_calls[-1][:2] == ["--delete-attachment", "1"] or any(
        c[:2] == ["--delete-attachment", "1"] for c in mkv_file.propedit_calls)
    assert vf.metadata.cover_remove is False and vf.metadata.cover_info == ""


# -- the command line ----------------------------------------------------------------------------------------------------

@needs_ffmpeg
def test_cli_refuses_without_yes_dry_run_shows_the_plan_and_yes_clears(tmp_path, monkeypatch, capsys):
    from redactor_common.cli import CliError
    from videocli.main import main
    import core.config as config

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "s.ini")
    clip = tmp_path / "Song.mp4"
    shutil.copy(SAMPLE, clip)
    vf = VideoFile(path=clip)
    vf.load()
    fill(vf)
    vf.save()
    assert not vf.save_error
    with pytest.raises(CliError, match="refusing to clear without --yes"):
        main(["clear", str(clip)])
    assert main(["clear", str(clip), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "planned" in out and "title" in out and "tmdb_id" in out
    kept = VideoFile(path=clip)
    kept.load()
    assert kept.metadata.title == "Song" and kept.metadata.cover_info  # a dry run wrote nothing
    with pytest.raises(CliError, match="unknown group"):
        main(["clear", str(clip), "--groups", "everything", "--yes"])
    assert main(["clear", str(clip), "--yes"]) == 0
    done = VideoFile(path=clip)
    done.load()
    assert done.metadata.title == "" and done.metadata.tmdb_id == "" and done.metadata.cover_info == ""
    assert done.metadata.content_type is ContentType.MUSIC_VIDEO  # type not named: kept
    assert main(["clear", str(clip), "--groups", "type", "--yes", "--json"]) == 0
    capsys.readouterr()
    final = VideoFile(path=clip)
    final.load()
    assert final.metadata.content_type_from_media_type  # cleared; stik 6 was left alone and shows through
    assert main(["clear", str(clip), "--yes"]) == 0  # nothing left: unchanged, still exit 0
    assert "unchanged" in capsys.readouterr().out
