"""
Edits keep the selection on the same FILES (2026-09-29).

_refresh_table_rows() refills rows in self.video_files order and then
re-sorts, so in a table sorted by a column a selection kept by row
number landed on a different file: Apply on S01E01 left S01E03 selected
and the next Apply silently edited S01E03. Also covers the family's
stale-panel check (cbzredactor 2026-09-29#05): an edit made outside the
panel must survive clicking another file.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtCore import Qt  # noqa: E402

import gui.main_window as mw  # noqa: E402
from core.video_file import VideoFile  # noqa: E402
from core.video_metadata import VideoMetadata  # noqa: E402
from tests.test_batch_operations_and_undo import _app, window  # noqa: E402,F401


def _row_files(window):
    return [window.table.item(row, 0).data(mw.FILE_ROLE) for row in range(window.table.rowCount())]


def _selected_names(window):
    return [vf.path.name for vf in window._selected_video_files()]


def _select(window, vf):
    window.table.selectRow(_row_files(window).index(vf))
    _app.processEvents()


@pytest.fixture
def sorted_window(window, tmp_path):
    path = tmp_path / "The Office S01E03.mkv"
    path.write_bytes(b"")
    window.video_files.append(VideoFile(path=path, metadata=VideoMetadata(show_title="the office", title="health care")))
    window._refresh_table_rows()
    window.table.sortByColumn(0, Qt.SortOrder.DescendingOrder)
    return window


def test_a_second_apply_edits_the_same_file(sorted_window):
    window = sorted_window
    first, second, third = window.video_files
    _select(window, first)
    window._on_apply_to_selected({"genre": "Comedy"})
    assert _selected_names(window) == [first.path.name]
    window._on_apply_to_selected({"network": "NBC"})
    assert [vf.metadata.network for vf in (first, second, third)] == ["NBC", "", ""]


def test_an_edit_to_the_selected_file_survives_clicking_another(sorted_window, monkeypatch):
    window = sorted_window
    first, second, _third = window.video_files
    _select(window, first)
    dialog = mw.SearchReplaceDialog
    monkeypatch.setattr(dialog, "exec", lambda d: dialog.DialogCode.Accepted)
    monkeypatch.setattr(dialog, "result_field_key", lambda d: "title")
    monkeypatch.setattr(dialog, "accepted_changes", lambda d: {0: "New Name"})
    window._on_search_replace()
    assert _selected_names(window) == [first.path.name]
    _select(window, second)
    assert first.metadata.title == "New Name"
