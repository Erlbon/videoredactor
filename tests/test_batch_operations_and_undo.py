"""
Video's batch operations on redactor_common's shared dialogs, and the
undo/redo this project gained with them (2026-09-23). The dialogs'
exec() is patched to "accept" so the real handlers run headless.
"""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog  # noqa: E402

from core.video_file import VideoFile  # noqa: E402
from core.video_metadata import ContentType, VideoMetadata  # noqa: E402

_app = QApplication.instance() or QApplication([])


@pytest.fixture
def window(monkeypatch, tmp_path):
    import core.config as config
    import gui.main_window as mw

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")
    monkeypatch.setattr(mw.MainWindow, "_restore_last_folder_on_startup", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_check_external_tools_on_startup", lambda self: None)
    w = mw.MainWindow()
    files = []
    for i, (show, title) in enumerate([("the office", "pilot"), ("the office", "diversity day")]):
        path = tmp_path / f"The Office S01E0{i + 1}.mkv"
        path.write_bytes(b"")
        files.append(VideoFile(path=path, metadata=VideoMetadata(show_title=show, title=title)))
    w.video_files = files
    w._refresh_table_rows()
    yield w
    # Closing with unsaved edits would open the modal "discard?" prompt.
    for vf in w.video_files:
        vf.dirty = False
    w.close()


def _auto_accept(monkeypatch, dialog_cls, configure=None):
    def fake_exec(self):
        if configure:
            configure(self)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(dialog_cls, "exec", fake_exec)


def test_case_conversion_applies_and_undo_redo_round_trips(window, monkeypatch):
    import gui.main_window as mw

    def pick_title_case(dialog):
        dialog.field_combo.setCurrentIndex(dialog.field_combo.findData("title"))
        dialog.mode_combo.setCurrentIndex(dialog.mode_combo.findText("Title Case"))

    _auto_accept(monkeypatch, mw.CaseConversionDialog, pick_title_case)
    window._on_case_conversion()
    assert [vf.metadata.title for vf in window.video_files] == ["Pilot", "Diversity Day"]
    assert all(vf.dirty for vf in window.video_files)
    assert window.undo_action.isEnabled() and "Case Conversion" in window.undo_action.text()

    window.undo_last_action()
    assert [vf.metadata.title for vf in window.video_files] == ["pilot", "diversity day"]
    assert not any(vf.dirty for vf in window.video_files)
    assert window.redo_action.isEnabled()

    window.redo_last_action()
    assert [vf.metadata.title for vf in window.video_files] == ["Pilot", "Diversity Day"]


def test_parse_filename_fills_typed_fields(window, monkeypatch):
    import gui.main_window as mw

    def set_pattern(dialog):
        dialog.pattern_edit.setText("The Office S%season_number%E%episode_number%")

    _auto_accept(monkeypatch, mw.ParseFilenameDialog, set_pattern)
    window._on_import_metadata_from_filename()
    assert [(vf.metadata.season_number, vf.metadata.episode_number) for vf in window.video_files] == [(1, 1), (1, 2)]
    window.undo_last_action()
    assert window.video_files[0].metadata.episode_number is None


def test_auto_numbering_into_an_int_field(window, monkeypatch):
    import gui.main_window as mw

    def pick_episode(dialog):
        dialog.field_combo.setCurrentIndex(dialog.field_combo.findData("episode_number"))
        dialog.start_spin.setValue(7)

    _auto_accept(monkeypatch, mw.AutoNumberingDialog, pick_episode)
    window._on_auto_numbering()
    assert [vf.metadata.episode_number for vf in window.video_files] == [7, 8]


def test_rename_by_pattern_renames_on_disk(window, monkeypatch):
    import gui.main_window as mw

    def set_pattern(dialog):
        dialog.pattern_edit.setText("%title%")

    _auto_accept(monkeypatch, mw.RenamePatternDialog, set_pattern)
    window._on_rename_by_pattern()
    names = sorted(vf.path.name for vf in window.video_files)
    assert names == ["diversity day.mkv", "pilot.mkv"]
    assert all(vf.path.exists() for vf in window.video_files)


def test_set_field_from_text_rejects_bad_values():
    import gui.main_window as mw

    vf = VideoFile(path=Path("x.mkv"))
    assert mw._set_field_from_text(vf, "episode_number", "03") and vf.metadata.episode_number == 3
    assert not mw._set_field_from_text(vf, "episode_number", "three")
    assert vf.metadata.episode_number == 3
    assert mw._set_field_from_text(vf, "content_type", "Movie") and vf.metadata.content_type is ContentType.MOVIE
    assert not mw._set_field_from_text(vf, "content_type", "Podcast")


def test_loading_a_folder_clears_undo(window, monkeypatch, tmp_path):
    window._push_undo("Bulk Edit", window.video_files)
    assert window.undo_manager.can_undo()
    monkeypatch.setattr("gui.main_window.discover_video_files", lambda folder, recursive: [])
    window._load_folder(tmp_path, recursive=False)
    assert not window.undo_manager.can_undo()
    assert not window.undo_action.isEnabled()
