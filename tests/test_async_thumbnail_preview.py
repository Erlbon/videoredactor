"""
The selected file's thumbnail is generated off the GUI thread
(2026-09-23). Before, MainWindow._update_preview() called
VideoFile.get_thumbnail() directly, which runs ffmpeg the first time a
file is previewed -- freezing the window for as long as ffmpeg took.
"""

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice  # noqa: E402
from PyQt6.QtGui import QColor, QImage  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])


class _FakeVideoFile:
    load_error = None

    def __init__(self, thumb_path, delay=0.0):
        self._thumb_path = thumb_path
        self._delay = delay

    def get_thumbnail(self):
        time.sleep(self._delay)
        return self._thumb_path


def _write_jpeg(path):
    img = QImage(64, 36, QImage.Format.Format_RGB32)
    img.fill(QColor(10, 120, 200))
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "JPEG")
    path.write_bytes(bytes(data))


def _window(monkeypatch):
    import gui.main_window as mw

    monkeypatch.setattr(mw.MainWindow, "_restore_last_folder_on_startup", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_check_external_tools_on_startup", lambda self: None)
    return mw.MainWindow()


def test_selecting_a_file_does_not_wait_for_ffmpeg(monkeypatch, tmp_path):
    thumb = tmp_path / "thumb.jpg"
    _write_jpeg(thumb)
    window = _window(monkeypatch)
    slow = _FakeVideoFile(thumb, delay=0.5)

    started = time.monotonic()
    window._update_preview([slow])
    assert time.monotonic() - started < 0.2  # returned without running the 0.5s "ffmpeg"
    assert "Loading" in window.tag_panel.preview_label.text()

    window._preview_loader.wait_for_done()
    deadline = time.monotonic() + 5
    while window.tag_panel.preview_label._original_pixmap is None and time.monotonic() < deadline:
        _app.processEvents()
        time.sleep(0.01)
    assert window.tag_panel.preview_label._original_pixmap is not None
    window.close()


def test_multi_selection_cancels_a_pending_preview(monkeypatch, tmp_path):
    thumb = tmp_path / "thumb.jpg"
    _write_jpeg(thumb)
    window = _window(monkeypatch)
    window._update_preview([_FakeVideoFile(thumb)])
    window._update_preview([_FakeVideoFile(thumb), _FakeVideoFile(thumb)])
    window._preview_loader.wait_for_done()
    for _ in range(20):
        _app.processEvents()
    assert window.tag_panel.preview_label._original_pixmap is None
    assert window.tag_panel.preview_label.text() == "2 files selected"
    window.close()
