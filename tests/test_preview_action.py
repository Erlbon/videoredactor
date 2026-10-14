"""Media > Preview...: wiring only. QMediaPlayer is never created here (it crashes the pytest process at exit
about one run in three); show_media_preview is replaced by a recorder."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QItemSelectionModel  # noqa: E402
from PyQt6.QtGui import QKeySequence  # noqa: E402

import redactor_common.gui.media_preview as media_preview  # noqa: E402
from tests.test_batch_operations_and_undo import _app, window  # noqa: E402,F401


def _select(window, rows):
    window.table.selectionModel().clearSelection()
    for row in rows:
        window.table.selectionModel().select(
            window.table.model().index(row, 0),
            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
        )


def test_preview_action_exists_with_safe_shortcut(window):
    action = window.actions_by_key["preview"]
    assert action.shortcut() == QKeySequence("Ctrl+Shift+P")
    assert "Alt" not in action.shortcut().toString()


def test_one_selected_file_opens_the_preview(window, monkeypatch):
    calls = []
    monkeypatch.setattr(media_preview, "show_media_preview", lambda parent, path, video=False: calls.append((path, video)))
    _select(window, [0])
    window.actions_by_key["preview"].trigger()
    assert calls == [(str(window.video_files[0].path), True)]


def test_zero_or_several_selected_files_only_say_so(window, monkeypatch):
    calls = []
    monkeypatch.setattr(media_preview, "show_media_preview", lambda *a, **k: calls.append(a))
    for rows in ([], [0, 1]):
        _select(window, rows)
        window.status_bar.clearMessage()
        window.actions_by_key["preview"].trigger()
        assert "exactly one file" in window.status_bar.currentMessage()
    assert calls == []
