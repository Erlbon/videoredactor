"""Drag-to-reorder of the side-panel fields (redactor_common's field_order), in the real MainWindow offscreen:
a real mouse drag on a grip moves the field, the order is saved by field key, a second window restores it, a Content
Type filter shows the relevant fields in the saved relative order, Export/Import carries it, Reset restores the
default, and editing two files after a reorder writes the right values to the right fields (real MP4s)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PyQt6.QtGui import QMouseEvent  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QFormLayout  # noqa: E402
from redactor_common.core import settings_bundle as sb  # noqa: E402

import core.config as config  # noqa: E402
from core.settings_adapter import VideoSettingsAdapter  # noqa: E402
from core.video_file import VideoFile  # noqa: E402
from core.video_metadata import ContentType  # noqa: E402
from tests.test_clear_fields import needs_ffmpeg, select  # noqa: E402
from tests.test_per_file_confirmation import close, make_window  # noqa: E402

_app = QApplication.instance() or QApplication([])


def _plain_window(monkeypatch, tmp_path):
    import gui.main_window as mw

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")
    monkeypatch.setattr(mw.MainWindow, "_restore_last_folder_on_startup", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_check_external_tools_on_startup", lambda self: None)
    w = mw.MainWindow()
    w.resize(1300, 1000)
    w.show()
    _app.processEvents()
    return w


def _shown(window):
    _app.processEvents()
    return window.tag_panel._field_order.visible_keys()


def _layout_keys(window):
    panel = window.tag_panel
    form = panel.form_layout
    by_widget = {id(w): k for k, w in panel._inputs.items()}
    return [by_widget[id(form.itemAt(i, QFormLayout.ItemRole.FieldRole).widget())] for i in range(form.rowCount())]


def _drag(window, key, to_key, below=False):
    panel = window.tag_panel
    grip = panel._field_order.grip(key)
    assert grip is not None and grip.isVisible()
    target = panel._inputs[to_key]
    target_global = target.mapToGlobal(QPoint(5, target.height() - 1 if below else 1))
    start = QPoint(grip.width() // 2, grip.height() // 2)
    QTest.mousePress(grip, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    for point in (grip.mapToGlobal(start) + QPoint(0, 12), target_global):
        QApplication.sendEvent(grip, QMouseEvent(
            QEvent.Type.MouseMove, QPointF(grip.mapFromGlobal(point)), QPointF(point),
            Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    QTest.mouseRelease(grip, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, grip.mapFromGlobal(target_global))
    _app.processEvents()


def test_drag_moves_the_field_and_saves_by_key(monkeypatch, tmp_path):
    w = _plain_window(monkeypatch, tmp_path)
    before = _shown(w)
    assert before[:3] == ["content_type", "title", "sort_title"]
    _drag(w, "sort_title", "content_type")  # a required field may be moved too: put another one above it
    after = _shown(w)
    assert after[:3] == ["sort_title", "content_type", "title"]
    assert _layout_keys(w) == after
    assert config.get_setting("panel", "field_order", "").split(",")[:3] == ["sort_title", "content_type", "title"]
    w.close()


def test_a_second_window_restores_the_order_and_reset_restores_the_default(monkeypatch, tmp_path):
    w = _plain_window(monkeypatch, tmp_path)
    default = _shown(w)
    _drag(w, "sort_title", "content_type")
    moved = _shown(w)
    w.close()
    second = _plain_window(monkeypatch, tmp_path)
    assert _shown(second) == moved
    second.tag_panel.reset_field_order()
    assert _shown(second) == default and config.get_setting("panel", "field_order", "") == ""
    second.close()


def test_a_content_type_filter_keeps_the_saved_relative_order(monkeypatch, tmp_path):
    w = _plain_window(monkeypatch, tmp_path)
    _drag(w, "title", "description", below=True)  # Title now sits below Description
    full = _shown(w)
    assert full.index("title") > full.index("description")
    w.tag_panel.set_content_type_filter(ContentType.MOVIE)
    movie = _shown(w)
    assert 0 < len(movie) < len(full)
    assert movie == [k for k in full if k in movie]  # the relevant fields, in the saved relative order
    w.tag_panel.set_content_type_filter(None)
    assert _shown(w) == full
    w.close()


def test_order_travels_through_export_import(monkeypatch, tmp_path):
    w = _plain_window(monkeypatch, tmp_path)
    default = _shown(w)
    _drag(w, "sort_title", "content_type")
    wanted = _shown(w)
    adapter = VideoSettingsAdapter()
    chosen = sb.default_selection(adapter)
    bundle = sb.parse_bundle(sb.dump_bundle(sb.build_bundle(adapter, chosen)), "videoredactor")
    w.tag_panel.reset_field_order()
    assert _shown(w) == default
    result = sb.apply_bundle(adapter, bundle, chosen)
    w._after_settings_import(result)
    assert _shown(w) == wanted
    w.close()


@needs_ffmpeg
def test_edit_two_files_after_a_reorder_writes_the_right_values_to_the_right_fields(monkeypatch, tmp_path):
    w = make_window(monkeypatch, tmp_path, ["One.mp4", "Two.mp4"], real=True)
    w.resize(1300, 1000)
    w.show()
    _app.processEvents()
    default = _shown(w)
    _drag(w, "title", default[-1], below=True)  # Title to the very end
    _drag(w, "director", default[0])  # Director to the top
    one, two = w.video_files
    select(w, one, two)
    panel = w.tag_panel
    panel._inputs["title"].setText("Shared Title")
    panel._inputs["director"].setText("Shared Director")
    assert panel._touched == {"title", "director"}  # the touched markers stayed with their own fields
    w._on_save_all()
    for vf in (one, two):
        assert not vf.save_error, vf.save_error
        fresh = VideoFile(path=vf.path)
        fresh.load()
        assert fresh.metadata.title == "Shared Title" and fresh.metadata.director == "Shared Director"
    close(w)
