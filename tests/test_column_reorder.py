"""Tools > Columns... reorders the table's columns (redactor_common's ColumnSettingsDialog), in the real
MainWindow: the dialog lists the table's CURRENT visual order (a header drag included), the new order is applied to
the table and persisted by key, survives a second window and Export/Import Settings, and the hidden set and widths
are kept."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402
from redactor_common.core import settings_bundle as sb  # noqa: E402
from redactor_common.gui.column_settings_dialog import ColumnSettingsDialog  # noqa: E402

import core.config as config  # noqa: E402
from core.settings_adapter import VideoSettingsAdapter  # noqa: E402

_app = QApplication.instance() or QApplication([])


def _make_window(monkeypatch, tmp_path):
    import gui.main_window as mw

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")
    monkeypatch.setattr(mw.MainWindow, "_restore_last_folder_on_startup", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_check_external_tools_on_startup", lambda self: None)
    return mw.MainWindow()


def _visual(window):
    return window._current_visual_column_order()


def _run_dialog(monkeypatch, window, edit):
    seen = {}

    def fake_exec(self):
        seen["order"] = self.column_order()
        edit(self)
        return 1

    monkeypatch.setattr(ColumnSettingsDialog, "exec", fake_exec)
    window._on_open_column_visibility()
    return seen


def _select(dialog, key):
    dialog.list_widget.setCurrentRow(dialog.column_order().index(key))


def test_dialog_lists_visual_order_including_a_header_drag(monkeypatch, tmp_path):
    window = _make_window(monkeypatch, tmp_path)
    header = window.table.horizontalHeader()
    header.moveSection(header.visualIndex(window._column_order.index("title")), 6)
    expected = _visual(window)
    seen = _run_dialog(monkeypatch, window, lambda d: None)
    assert seen["order"] == expected
    assert seen["order"] != window._column_order
    window.close()


def test_move_in_dialog_reorders_table_and_persists(monkeypatch, tmp_path):
    window = _make_window(monkeypatch, tmp_path)
    window._on_column_visibility_toggled("genre_tags", False)  # hidden by the user
    before = _visual(window)

    def edit(dialog):
        _select(dialog, "title")
        for _ in range(2):
            dialog.up_button.click()

    _run_dialog(monkeypatch, window, edit)
    expected = list(before)
    expected.remove("title")
    expected.insert(before.index("title") - 2, "title")
    assert _visual(window) == expected
    assert window._column_order == expected
    assert window._load_persisted_column_order() == expected
    assert window.table.isColumnHidden(window._column_order.index("genre_tags"))
    window.close()
    second = _make_window(monkeypatch, tmp_path)
    assert _visual(second) == expected
    assert second.table.isColumnHidden(second._column_order.index("genre_tags"))
    second.close()


def test_move_after_a_header_drag_uses_the_shown_order(monkeypatch, tmp_path):
    window = _make_window(monkeypatch, tmp_path)
    header = window.table.horizontalHeader()
    header.moveSection(header.visualIndex(window._column_order.index("title")), 6)
    dragged = _visual(window)
    assert window._load_persisted_column_order() == dragged  # the header drag persisted

    def edit(dialog):
        _select(dialog, "title")
        dialog.up_button.click()

    _run_dialog(monkeypatch, window, edit)
    expected = list(dragged)
    expected.remove("title")
    expected.insert(dragged.index("title") - 1, "title")
    assert _visual(window) == expected
    assert [window._column_order[header.logicalIndex(v)] for v in range(header.count())] == expected
    window.close()


def test_unchecking_keeps_order(monkeypatch, tmp_path):
    window = _make_window(monkeypatch, tmp_path)
    header = window.table.horizontalHeader()
    header.moveSection(header.visualIndex(window._column_order.index("title")), 6)
    order = _visual(window)

    def edit(dialog):
        for i in range(dialog.list_widget.count()):
            if dialog.list_widget.item(i).data(Qt.ItemDataRole.UserRole) == "genre_tags":
                dialog.list_widget.item(i).setCheckState(Qt.CheckState.Unchecked)

    _run_dialog(monkeypatch, window, edit)
    assert _visual(window) == order
    assert window.table.isColumnHidden(window._column_order.index("genre_tags"))
    window.close()


def test_order_travels_through_export_import(monkeypatch, tmp_path):
    window = _make_window(monkeypatch, tmp_path)
    header = window.table.horizontalHeader()
    header.moveSection(header.visualIndex(window._column_order.index("title")), 6)
    wanted = _visual(window)
    adapter = VideoSettingsAdapter()
    chosen = sb.default_selection(adapter)
    bundle = sb.parse_bundle(sb.dump_bundle(sb.build_bundle(adapter, chosen)), "videoredactor")
    config.set_setting("table", "column_order", "")  # lose the saved order
    window._rebuild_table_columns()
    assert _visual(window) != wanted
    result = sb.apply_bundle(adapter, bundle, chosen)
    window._after_settings_import(result)
    assert _visual(window) == wanted
    window.close()
