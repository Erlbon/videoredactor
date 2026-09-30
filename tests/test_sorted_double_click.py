"""
Double-clicking a Filename cell renames the file in THAT row, even in a
sorted table (2026-09-30). The handler used to index self.video_files by
the visual row, so after sorting it renamed a different file.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt  # noqa: E402

import gui.main_window as mw  # noqa: E402
from core.video_file import VideoFile  # noqa: E402
from core.video_metadata import VideoMetadata  # noqa: E402
from tests.test_batch_operations_and_undo import window  # noqa: E402,F401


def test_double_click_acts_on_the_sorted_row(window, tmp_path, monkeypatch):
    for name in ("b.mkv", "a.mkv", "c.mkv"):
        path = tmp_path / name
        path.write_bytes(b"")
        window.video_files.append(VideoFile(path=path, metadata=VideoMetadata(title=name)))
    window._refresh_table_rows()
    window.table.sortByColumn(0, Qt.SortOrder.DescendingOrder)
    seen = []
    monkeypatch.setattr(window, "rename_single_file", seen.append)
    col = window._column_order.index("filename")
    for row in range(window.table.rowCount()):
        expected = window.table.item(row, 0).data(mw.FILE_ROLE)
        window._on_cell_double_clicked(row, col)
        assert seen[-1] is expected
    assert len(seen) == window.table.rowCount() == len(window.video_files)
