"""The side panel's Play button: enabled only for exactly one file without a load error, and it runs the same
Play action as Media > Play (show_media_preview is stubbed, so no QMediaPlayer is created)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from core.video_file import VideoFile  # noqa: E402

_app = QApplication.instance() or QApplication([])


@pytest.fixture
def window(monkeypatch, tmp_path):
    import core.config as config
    import gui.main_window as mw

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")
    monkeypatch.setattr(mw.MainWindow, "_restore_last_folder_on_startup", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_check_external_tools_on_startup", lambda self: None)
    w = mw.MainWindow()
    yield w
    w.close()


def _load(window, tmp_path, *names):
    files = []
    for name in names:
        (tmp_path / name).write_bytes(b"")
        files.append(VideoFile(path=tmp_path / name))
    window.video_files = files
    window._refresh_table_rows()
    return files


def _select(window, rows):
    window.table.clearSelection()
    for r in rows:
        window.table.selectRow(r) if len(rows) == 1 else window.table.selectionModel().select(
            window.table.model().index(r, 0),
            window.table.selectionModel().SelectionFlag.Select | window.table.selectionModel().SelectionFlag.Rows,
        )


def test_button_has_tooltip_and_starts_disabled(window):
    b = window.tag_panel.play_button
    assert not b.isEnabled()
    assert b.toolTip() == "Play the selected file (Ctrl+Shift+P)"
    assert b.minimumHeight() >= 40 and b.text().strip() == "Play" and not b.icon().isNull()


def test_enabled_for_one_file_only(window, tmp_path):
    _load(window, tmp_path, "a.mp4", "b.mp4")
    b = window.tag_panel.play_button
    _select(window, [])
    assert not b.isEnabled()
    _select(window, [0])
    assert b.isEnabled()
    _select(window, [0, 1])
    assert not b.isEnabled()


def test_disabled_for_a_load_error_file(window, tmp_path):
    files = _load(window, tmp_path, "a.mp4")
    files[0].load_error = "boom"
    _select(window, [0])
    assert not window.tag_panel.play_button.isEnabled()


def test_click_runs_the_play_action(window, tmp_path, monkeypatch):
    files = _load(window, tmp_path, "a.mp4")
    calls = []
    import redactor_common.gui.media_preview as mp

    monkeypatch.setattr(mp, "show_media_preview", lambda parent, path, video=True, autoplay=False: calls.append((path, video, autoplay)))
    _select(window, [0])
    window.tag_panel.play_button.click()
    assert calls == [(files[0].path, True, True)]
