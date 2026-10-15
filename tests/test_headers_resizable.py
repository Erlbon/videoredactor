"""House rule (owner, 2026-10-15): every table column can be resized by dragging. The lint checks the sources;
the rest builds the real table dialogs offscreen with real data."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QHeaderView  # noqa: E402
from redactor_common.gui.table_columns import lint_resizable_headers  # noqa: E402

from core.file_check import DAMAGED, NOTE, REPAIRABLE, CheckResult, Finding  # noqa: E402
from core.video_file import VideoFile  # noqa: E402

_app = QApplication.instance() or QApplication([])

REPO = Path(__file__).parent.parent


def test_no_undraggable_header_modes_in_gui():
    assert lint_resizable_headers(REPO / "gui") == []


def _assert_draggable(table):
    header = table.horizontalHeader()
    for section in range(header.count() - 1):
        assert header.sectionResizeMode(section) == QHeaderView.ResizeMode.Interactive, section
    for section in range(header.count()):
        header.resizeSection(section, 123)
        assert header.sectionSize(section) == 123 or section == header.count() - 1  # last one also fills the rest


def test_file_check_dialog_columns_draggable(tmp_path):
    from gui.file_check_dialog import FileCheckResultsDialog

    files = []
    for name, sev in (("a.mkv", DAMAGED), ("b.mp4", REPAIRABLE), ("c.mkv", NOTE)):
        vf = VideoFile(path=tmp_path / name)
        vf.check = CheckResult(findings=[Finding("x", sev, "something is wrong with this file " * 3)])
        files.append(vf)
    dialog = FileCheckResultsDialog(files)
    dialog.show()
    assert dialog.table.rowCount() == 3
    _assert_draggable(dialog.table)
    dialog.close()


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


def test_main_window_columns_draggable_and_persisted_widths_win(window, tmp_path):
    import json

    from core.config import set_setting

    files = []
    for name in ("one.mp4", "two.mp4"):
        (tmp_path / name).write_bytes(b"")
        files.append(VideoFile(path=tmp_path / name))
    window.video_files = files
    set_setting("table", "column_widths", json.dumps({"filename": 333}))
    window._rebuild_table_columns()
    window.show()
    header = window.table.horizontalHeader()
    visible = [i for i in range(header.count()) if not header.isSectionHidden(i)]
    for section in visible[:-1]:
        assert header.sectionResizeMode(section) == QHeaderView.ResizeMode.Interactive
    name_col = window._column_order.index("filename")
    assert header.sectionSize(name_col) == 333  # the persisted width wins over the starting widths
    header.resizeSection(name_col, 200)
    assert header.sectionSize(name_col) == 200
    # the funnel still sits inside its (resized) section
    rect = window.filter_header.funnel_rect(name_col)
    assert rect is not None
    assert header.sectionViewportPosition(name_col) <= rect.left() < header.sectionViewportPosition(name_col) + 200
    window.close()
