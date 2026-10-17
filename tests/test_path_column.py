"""The shared Path column (redactor_common.core.path_column) in the real MainWindow: directly after Filename,
visible by default, shows the folder, full path in the tooltip and on Ctrl+C, sorts as text, has a text filter,
is migrated once for old saved column settings, and follows a rename and a move into folders."""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402
from redactor_common.core.path_column import lint_path_column  # noqa: E402

from redactor_common.core.move_plan import PlannedMove  # noqa: E402

from core.video_file import VideoFile  # noqa: E402

_app = QApplication.instance() or QApplication([])
NL = chr(10)


def _make_window(monkeypatch, tmp_path, seed=None):
    import core.config as config
    import gui.main_window as mw

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")
    if seed:
        for key, value in seed.items():
            config.set_setting("table", key, value)
    monkeypatch.setattr(mw.MainWindow, "_restore_last_folder_on_startup", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_check_external_tools_on_startup", lambda self: None)
    return mw.MainWindow()


def _files(tmp_path, names=("a.mp4", "b.mp4")):
    out = []
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
        out.append(VideoFile(path=path))
    return out


@pytest.fixture
def window(monkeypatch, tmp_path):
    w = _make_window(monkeypatch, tmp_path)
    w.video_files = _files(tmp_path, ("one/a.mp4", "two/b.mp4"))
    w._refresh_table_rows()
    yield w
    w.close()


def _col(w, key="path"):
    return w._column_order.index(key)


def test_path_is_directly_after_filename_and_visible_by_default(window):
    assert lint_path_column(window._column_order) == []
    assert _col(window) == _col(window, "filename") + 1
    assert not window.table.isColumnHidden(_col(window))
    assert window.table.horizontalHeaderItem(_col(window)).text() == "Path"


def test_cell_shows_the_folder_and_the_tooltip_the_full_path(window, tmp_path):
    rows = {window._file_for_row(r).path.name: r for r in range(window.table.rowCount())}
    item = window.table.item(rows["a.mp4"], _col(window))
    assert item.text() == str(tmp_path / "one")
    assert item.toolTip().splitlines()[0] == str(tmp_path / "one" / "a.mp4")


def test_ctrl_c_text_is_the_full_path(window):
    # The key handling itself (real Ctrl+C, real clipboard) is tested in redactor_common; delivering a real Ctrl+C
    # here made two unrelated TheTVDB tests of test_per_file_confirmation fail later in the same process (cause not
    # found), so this checks the wiring: the copy filter is installed and the text it would copy is the full path.
    from redactor_common.gui.path_column import selected_column_text

    assert getattr(window.table, "_cell_copy_filter", None) is not None
    window.table.selectRow(0)
    window.table.setCurrentCell(0, _col(window))
    assert selected_column_text(window.table) == str(window._file_for_row(0).path)


def test_sorting_the_path_column_is_text_order(window, tmp_path):
    window.table.sortItems(_col(window), Qt.SortOrder.DescendingOrder)
    names = [window._file_for_row(r).path.name for r in range(window.table.rowCount())]
    assert names == ["b.mp4", "a.mp4"]  # two/ before one/ when descending
    window.table.sortItems(_col(window), Qt.SortOrder.AscendingOrder)
    names = [window._file_for_row(r).path.name for r in range(window.table.rowCount())]
    assert names == ["a.mp4", "b.mp4"]


def test_the_path_column_has_a_text_filter(window):
    assert window.filter_header.funnel_rect(_col(window)) is not None
    window.filter_header.set_text_filter("path", "two")
    shown = [window._file_for_row(r).path.name for r in range(window.table.rowCount())
             if not window.table.isRowHidden(r)]
    assert shown == ["b.mp4"]


def test_old_saved_order_and_hidden_set_are_migrated_once(monkeypatch, tmp_path):
    old_order = "filename,status,content_type,title,duration_seconds,path,sources"
    w = _make_window(monkeypatch, tmp_path, seed={"column_order": old_order, "hidden_columns": "path,sources"})
    try:
        assert _col(w) == _col(w, "filename") + 1
        assert not w.table.isColumnHidden(_col(w))
        assert w.table.isColumnHidden(_col(w, "sources"))  # other choices stay
        import core.config as config

        assert config.get_setting("table", "path_column_seeded", "") == "1"
        # the user hides it again afterwards: the choice now wins on the next rebuild
        w._on_column_visibility_toggled("path", False)
        w._rebuild_table_columns()
        assert w.table.isColumnHidden(_col(w))
    finally:
        w.close()


def test_a_path_cell_follows_a_rename(window, tmp_path, monkeypatch):
    import gui.main_window as mw

    vf = window._file_for_row(0)
    new_dir = tmp_path / "renamed"
    new_dir.mkdir()

    def fake_prompt(parent, path, setter, log=None):
        setter(str(new_dir / "z.mp4"))
        return True

    monkeypatch.setattr(mw, "prompt_rename_single_file", fake_prompt)
    window.rename_single_file(vf)
    row = next(r for r in range(window.table.rowCount()) if window._file_for_row(r) is vf)
    item = window.table.item(row, _col(window))
    assert item.text() == str(new_dir)
    assert item.toolTip().splitlines()[0] == str(new_dir / "z.mp4")


def test_a_path_cell_follows_a_move_into_folders(window, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    vf = window._file_for_row(0)
    old = vf.path
    target = tmp_path / "Library" / "Show" / old.name
    window._move_into_folders(
        [PlannedMove(vf, str(old), str(target), [str(target.parent.parent), str(target.parent)], "", False, str(tmp_path))])
    assert vf.path == Path(target)
    row = next(r for r in range(window.table.rowCount()) if window._file_for_row(r) is vf)
    assert window.table.item(row, _col(window)).text() == str(target.parent)
