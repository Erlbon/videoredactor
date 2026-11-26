"""Delete Files (Recycle Bin) and the Delete key's question: Remove from List / Delete Files / Cancel.

The trash function and every dialog are replaced; the files live in pytest's tmp_path."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtCore import QItemSelectionModel  # noqa: E402
from PyQt6.QtGui import QKeySequence  # noqa: E402
from PyQt6.QtWidgets import QMessageBox  # noqa: E402

from redactor_common.core.trash import TrashError  # noqa: E402
from redactor_common.gui.remove_or_delete import DELETE, REMOVE  # noqa: E402
from tests.test_batch_operations_and_undo import _app, window  # noqa: E402,F401


def _select(window, rows):
    window.table.selectionModel().clearSelection()
    for row in rows:
        window.table.selectionModel().select(
            window.table.model().index(row, 0),
            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
        )


class Bin:
    """A fake Recycle Bin: records the paths and actually removes the files."""

    def __init__(self, fail=()):
        self.paths = []
        self.fail = set(fail)

    def __call__(self, path):
        if os.path.basename(path) in self.fail:
            raise TrashError("kept: test failure")
        self.paths.append(path)
        os.remove(path)


@pytest.fixture
def rig(window, monkeypatch):
    import gui.main_window as mw

    bin_ = Bin()
    monkeypatch.setattr(mw, "trash_with_fallback", lambda *a, **k: bin_)
    asked = {"question": 0, "delete_key": []}

    def question(*a, **k):
        asked["question"] += 1
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: asked.setdefault("warning", a[2])))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: asked.setdefault("info", a[2])))
    return window, bin_, asked


def _answer(monkeypatch, asked, choice):
    import gui.main_window as mw

    def fake(parent, count, names=(), noun="file", exec_box=None):
        asked["delete_key"].append((count, list(names), noun))
        return choice

    monkeypatch.setattr(mw, "ask_remove_or_delete", fake)


def test_key_bindings(window):
    keys = window.actions_by_key
    assert keys["delete_files"].shortcut() == QKeySequence("Shift+Del")
    assert keys["remove_from_list"].shortcut().isEmpty()
    assert keys["remove_or_delete"].shortcut() == QKeySequence("Del")
    assert keys["delete_files"].text() == "&Delete Files (Recycle Bin)…"
    assert "Recycle Bin" in keys["delete_files"].toolTip()


def test_remove_keeps_the_files(rig, monkeypatch):
    window, bin_, asked = rig
    _answer(monkeypatch, asked, REMOVE)
    paths = [vf.path for vf in window.video_files]
    _select(window, [0])
    window.on_delete_key()
    assert asked["delete_key"] == [(1, [paths[0].name], "video")]
    assert [vf.path for vf in window.video_files] == [paths[1]]
    assert bin_.paths == []
    assert all(p.exists() for p in paths)


def test_delete_trashes_the_files_without_a_second_question(rig, monkeypatch):
    window, bin_, asked = rig
    _answer(monkeypatch, asked, DELETE)
    paths = [vf.path for vf in window.video_files]
    _select(window, [0, 1])
    window.on_delete_key()
    assert asked["question"] == 0
    assert sorted(bin_.paths) == sorted(str(p) for p in paths)
    assert window.video_files == []
    assert not any(p.exists() for p in paths)
    assert "Sent 2 file(s)" in window.status_bar.currentMessage()
    assert "warning" not in asked


def test_cancel_changes_nothing(rig, monkeypatch):
    window, bin_, asked = rig
    _answer(monkeypatch, asked, None)
    paths = [vf.path for vf in window.video_files]
    _select(window, [0, 1])
    window.on_delete_key()
    assert len(window.video_files) == 2
    assert bin_.paths == []
    assert all(p.exists() for p in paths)


def test_nothing_selected_asks_nothing(rig, monkeypatch):
    window, bin_, asked = rig
    _answer(monkeypatch, asked, DELETE)
    _select(window, [])
    window.on_delete_key()
    assert asked["delete_key"] == []
    assert asked["question"] == 0
    assert "select the videos" in window.status_bar.currentMessage()
    assert len(window.video_files) == 2


def test_the_menu_item_still_confirms(rig):
    window, bin_, asked = rig
    paths = [vf.path for vf in window.video_files]
    _select(window, [1])
    window.actions_by_key["delete_files"].trigger()
    assert asked["question"] == 1
    assert bin_.paths == [str(paths[1])]
    assert [vf.path for vf in window.video_files] == [paths[0]]


def test_the_menu_item_answering_no_keeps_everything(rig, monkeypatch):
    window, bin_, asked = rig
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
    _select(window, [0])
    window.delete_files()
    assert bin_.paths == []
    assert len(window.video_files) == 2


def test_only_the_video_goes_not_its_sidecars(rig):
    window, bin_, asked = rig
    video = window.video_files[0].path
    sidecar = video.with_suffix(".srt")
    sidecar.write_text("1")
    _select(window, [0])
    window.delete_files(confirm=False)
    assert bin_.paths == [str(video)]
    assert sidecar.exists()


def test_a_video_with_unsaved_changes_is_refused(rig):
    window, bin_, asked = rig
    window.video_files[0].dirty = True
    _select(window, [0, 1])
    window.delete_files(confirm=False)
    assert bin_.paths == []
    assert len(window.video_files) == 2
    assert "unsaved changes" in asked["warning"]


def test_a_failure_is_reported_once_and_the_file_stays_in_the_list(window, monkeypatch):
    import gui.main_window as mw

    failing = window.video_files[0].path.name
    bin_ = Bin(fail={failing})
    monkeypatch.setattr(mw, "trash_with_fallback", lambda *a, **k: bin_)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(a[2])))
    _select(window, [0, 1])
    window.delete_files(confirm=False)
    assert len(warnings) == 1
    assert failing in warnings[0] and "1 of 2" in warnings[0]
    assert [vf.path.name for vf in window.video_files] == [failing]
    assert window.video_files[0].path.exists()


def test_the_players_window_is_closed_first(rig, monkeypatch):
    import redactor_common.gui.media_preview as media_preview

    window, bin_, asked = rig
    closed = []

    class FakePlayer:
        def __init__(self, path):
            self._path = path

        def close(self):
            closed.append(self._path)

    other = window.video_files[1].path
    mine = window.video_files[0].path
    monkeypatch.setattr(media_preview, "_open_windows", [FakePlayer(str(mine)), FakePlayer(str(other))])
    _select(window, [0])
    window.delete_files(confirm=False)
    assert closed == [str(mine)]


def test_the_row_menu_offers_delete_files(window):
    items = window._table_context_extra_items([window.video_files[0]])
    assert window.delete_files_act in items
