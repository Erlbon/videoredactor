"""The file table's column filters (a funnel in each filterable header section), replacing the old
'Filter by Content Type' row above the table. The real MainWindow, no QMediaPlayer."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QCheckBox, QToolBar  # noqa: E402

from core.video_file import VideoFile  # noqa: E402
from core.video_metadata import ContentType  # noqa: E402

_app = QApplication.instance() or QApplication([])

TYPES = {"a.mp4": ContentType.MOVIE, "b.mp4": ContentType.TV, "c.mp4": ContentType.TV, "d.mp4": ContentType.CLIP}


@pytest.fixture
def window(monkeypatch, tmp_path):
    import core.config as config
    import gui.main_window as mw

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")
    monkeypatch.setattr(mw.MainWindow, "_restore_last_folder_on_startup", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_check_external_tools_on_startup", lambda self: None)
    w = mw.MainWindow()
    files = []
    for name, content_type in TYPES.items():
        (tmp_path / name).write_bytes(b"")
        vf = VideoFile(path=tmp_path / name)
        vf.metadata.content_type = content_type
        files.append(vf)
    w.video_files = files
    w._refresh_table_rows()
    yield w
    w.close()


def _shown(w):
    return sorted(w._file_for_row(r).path.name for r in range(w.table.rowCount()) if not w.table.isRowHidden(r))


def test_the_old_filter_row_is_gone_and_all_columns_stay(window):
    import gui.main_window as mw

    assert not hasattr(window, "filter_combo")
    assert len(window._column_order) == len(mw.FIELD_LABELS) + len(mw.TECHNICAL_COLUMNS) + 3
    assert "director" in window.tag_panel._inputs and "show_title" in window.tag_panel._inputs


def test_filterable_columns(window):
    h = window.filter_header
    col = window._column_order.index
    assert h.funnel_rect(col("content_type")) is not None
    assert h.funnel_rect(col("filename")) is not None
    assert h.funnel_rect(col("duration_seconds")) is None
    assert h.funnel_rect(col("size_bytes")) is None


def test_content_type_filter_hides_rows_and_marks_the_header(window):
    h = window.filter_header
    ct = window._column_order.index("content_type")
    popup = h.build_popup(ct)
    assert [i.text() for i in popup.items()] == ["Clip", "Movie", "TV"]
    popup.set_checked_values({ContentType.TV.value})
    assert _shown(window) == ["b.mp4", "c.mp4"]
    assert h.is_active(ct)
    assert "Showing 2 of 4" in window.status_bar.currentMessage()
    window._clear_column_filters()
    assert _shown(window) == ["a.mp4", "b.mp4", "c.mp4", "d.mp4"] and not h.is_active(ct)


def test_selection_and_targets_follow_the_visible_rows(window):
    window.filter_header.set_value_filter("content_type", {ContentType.TV.value})
    window.table.selectAll()
    assert sorted(vf.path.name for vf in window._selected_video_files()) == ["b.mp4", "c.mp4"]
    window.table.clearSelection()
    assert [vf.path.name for vf in window._visible_video_files()] == ["b.mp4", "c.mp4"]
    assert len(window._batch_targets("edit")) == 2
    assert len(window.video_files) == 4  # hidden files stay loaded


def test_filter_survives_a_rebuild_and_a_sort(window):
    window.filter_header.set_text_filter("filename", "c.")
    window._refresh_table_rows()
    assert _shown(window) == ["c.mp4"]
    window.table.sortItems(0)
    assert _shown(window) == ["c.mp4"]


def test_selected_row_hidden_by_a_filter_is_deselected(window):
    window.table.selectAll()
    window.filter_header.set_value_filter("content_type", {ContentType.CLIP.value})
    assert [vf.path.name for vf in window._selected_video_files()] == ["d.mp4"]


def test_toolbar_include_subfolders_checkbox_sits_after_open_folder(window):
    tb = [t for t in window.findChildren(QToolBar) if t.actions()][0]
    actions = tb.actions()
    box = tb.widgetForAction(actions[actions.index(window.open_folder_action) + 1])
    assert isinstance(box, QCheckBox) and box is window.include_subfolders_box
    act = window.actions_by_key["include_subfolders"]
    box.setChecked(not act.isChecked())
    assert box.isChecked() == act.isChecked()
