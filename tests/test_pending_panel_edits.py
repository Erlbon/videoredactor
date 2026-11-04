"""
Typed-but-unapplied edits in the bulk-edit panel are never lost (2026-11-04, same as epubredactor 2026-11-04#01/#02
and cbzredactor): when the table selection changes they are applied to the PREVIOUS selection through the normal
Apply path (one undo entry, rows marked changed, a status-bar message), then the new selection is loaded. A refresh
of the same selection keeps them pending; Refresh List, Clear List, Open Folder and quitting count them as unsaved
changes. Real MainWindow, offscreen.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtCore import QItemSelection, QItemSelectionModel  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from core.video_file import VideoFile  # noqa: E402
from core.video_metadata import ContentType, VideoMetadata  # noqa: E402

_app = QApplication.instance() or QApplication([])


@pytest.fixture
def window(monkeypatch, tmp_path):
    import core.config as config
    import gui.main_window as mw

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")
    monkeypatch.setattr(mw.MainWindow, "_restore_last_folder_on_startup", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_check_external_tools_on_startup", lambda self: None)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    w = mw.MainWindow()
    files = []
    for name, ct in [("a.mkv", ContentType.MOVIE), ("b.mkv", ContentType.MOVIE), ("c.mkv", ContentType.MOVIE),
                     ("d.mkv", ContentType.TV)]:
        path = tmp_path / name
        path.write_bytes(b"")
        files.append(VideoFile(path=path, metadata=VideoMetadata(title=f"Old {name}", content_type=ct)))
    w.video_files = files
    w._refresh_table_rows()
    yield w
    for vf in w.video_files:
        vf.dirty = False
    w.close()


def _file(window, name):
    return next(vf for vf in window.video_files if vf.path.name == name)


def _select(window, *names):
    import gui.main_window as mw

    wanted = {name for name in names}
    model = window.table.model()
    selection = QItemSelection()
    for row in range(window.table.rowCount()):
        if window.table.item(row, 0).data(mw.FILE_ROLE).path.name in wanted:
            selection.select(model.index(row, 0), model.index(row, window.table.columnCount() - 1))
    window.table.selectionModel().select(selection, QItemSelectionModel.SelectionFlag.ClearAndSelect)
    _app.processEvents()


def _type(window, field, text):
    window.tag_panel._inputs[field].setText(text)
    assert window.tag_panel.has_pending_changes()


def test_selection_change_applies_pending_edit_to_the_previous_selection_with_one_undo(window):
    _select(window, "a.mkv", "b.mkv")
    _type(window, "studio", "Pending Pictures")
    assert not window.undo_manager.can_undo()

    _select(window, "c.mkv")

    a, b, c = (_file(window, n) for n in ("a.mkv", "b.mkv", "c.mkv"))
    assert a.metadata.studio == "Pending Pictures" and b.metadata.studio == "Pending Pictures"
    assert c.metadata.studio != "Pending Pictures"
    assert a.dirty and b.dirty and not c.dirty
    # the panel now shows C, with nothing pending
    assert window.tag_panel.current_files() == [c]
    assert window.tag_panel._inputs["studio"].text() == ""
    assert not window.tag_panel.has_pending_changes()

    window.undo_last_action()  # ONE undo restores both files
    assert a.metadata.studio == "" and b.metadata.studio == ""
    assert not a.dirty and not b.dirty
    assert not window.undo_manager.can_undo()


def test_status_bar_says_how_many_files_got_the_pending_edits(window):
    _select(window, "a.mkv", "b.mkv")
    _type(window, "studio", "Pending Pictures")
    _select(window, "c.mkv")
    assert window.status_bar.currentMessage() == "Applied pending edits to 2 files"


def test_no_auto_apply_when_the_previous_selection_was_empty(window):
    window.table.clearSelection()
    _app.processEvents()
    _type(window, "title", "Nobody Gets This")
    window.status_bar.clearMessage()
    _select(window, "c.mkv")
    assert all(vf.metadata.title != "Nobody Gets This" for vf in window.video_files)
    assert not any(vf.dirty for vf in window.video_files)
    assert not window.undo_manager.can_undo()
    assert window.status_bar.currentMessage() == ""


def test_nothing_pending_or_unchanged_values_make_no_undo_entry_and_no_message(window):
    _select(window, "a.mkv")
    window.status_bar.clearMessage()
    _select(window, "b.mkv")  # nothing typed
    assert not window.undo_manager.can_undo()
    _type(window, "title", "Old b.mkv x")
    _type(window, "title", "Old b.mkv")  # typed back to what is already there: still pending
    _select(window, "c.mkv")
    assert not window.undo_manager.can_undo()
    assert not any(vf.dirty for vf in window.video_files)
    assert window.status_bar.currentMessage() == ""


def test_moving_from_a_movie_to_a_tv_episode_applies_only_the_movie_panel_fields_to_the_movie(window):
    _select(window, "a.mkv")
    assert "director" in window.tag_panel._inputs and "show_title" not in window.tag_panel._inputs
    _type(window, "director", "Movie Director")
    _select(window, "d.mkv")
    a, d = _file(window, "a.mkv"), _file(window, "d.mkv")
    assert a.metadata.director == "Movie Director"
    assert d.metadata.director == "" and not d.dirty
    # the TV panel: its own fields, nothing pending
    assert "show_title" in window.tag_panel._inputs and "director" not in window.tag_panel._inputs
    assert not window.tag_panel.has_pending_changes()


def test_a_refresh_of_the_same_selection_keeps_the_pending_edit_pending(window):
    """Undo, a table rebuild, Show all fields, or any operation that re-reads the selection into the panel keeps
    what was typed (still pending, not applied), so nothing is lost and nothing is applied behind your back."""
    _select(window, "a.mkv", "b.mkv")
    window._on_apply_to_selected({"comment": "applied"})  # gives Undo something to do
    _type(window, "studio", "Still Pending")
    window._refresh_table_rows()
    window._on_selection_changed()
    window.undo_last_action()
    window.tag_panel.show_all_box.setChecked(True)
    window.tag_panel.refresh_fields()  # what a Settings change (genres, hidden columns) does
    _app.processEvents()
    panel = window.tag_panel
    assert panel.has_pending_changes()
    assert panel._inputs["studio"].text() == "Still Pending"
    assert _file(window, "a.mkv").metadata.studio == ""
    assert not window.undo_manager.can_undo()
    _select(window, "c.mkv")
    assert _file(window, "a.mkv").metadata.studio == "Still Pending"


def _record_questions(monkeypatch, answer):
    asked = []

    def question(*args, **kwargs):
        asked.append(args[2] if len(args) > 2 else "")
        return answer
    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    return asked


@pytest.mark.parametrize("command", ["_refresh_list", "_on_clear_list"])
def test_list_commands_ask_about_pending_edits_and_keep_them_when_cancelled(window, monkeypatch, command):
    """Refresh List / Clear List with only a typed, not Applied edit: it is applied first (undoable), so it counts
    as an unsaved change in the "discard?" question; answering No loses nothing."""
    _select(window, "a.mkv", "b.mkv")
    _type(window, "studio", "Asked About")
    asked = _record_questions(monkeypatch, QMessageBox.StandardButton.No)
    getattr(window, command)()
    assert len(asked) == 1 and "unsaved changes" in asked[0]
    assert len(window.video_files) == 4
    a, b = _file(window, "a.mkv"), _file(window, "b.mkv")
    assert a.metadata.studio == "Asked About" and b.metadata.studio == "Asked About"
    assert a.dirty and b.dirty
    window.undo_last_action()
    assert a.metadata.studio == ""


@pytest.mark.parametrize("command", ["_refresh_list", "_on_clear_list"])
def test_list_commands_without_pending_edits_ask_nothing(window, monkeypatch, command):
    _select(window, "a.mkv", "b.mkv")
    asked = _record_questions(monkeypatch, QMessageBox.StandardButton.No)
    getattr(window, command)()
    assert asked == []
    assert not window.undo_manager.can_undo()


def test_quitting_asks_about_pending_edits(window, monkeypatch):
    window.show()
    _select(window, "a.mkv")
    _type(window, "studio", "Do Not Lose Me")
    asked = _record_questions(monkeypatch, QMessageBox.StandardButton.No)
    window.close()
    assert len(asked) == 1 and window.isVisible()
    assert _file(window, "a.mkv").metadata.studio == "Do Not Lose Me"


def test_remove_from_list_drops_pending_edits_for_the_removed_files_without_an_undo_entry(window):
    _select(window, "a.mkv", "b.mkv")
    _type(window, "studio", "Gone Too")
    window._on_remove_from_list()
    _app.processEvents()
    assert [vf.path.name for vf in window.video_files] == ["c.mkv", "d.mkv"]
    assert all(vf.metadata.studio != "Gone Too" for vf in window.video_files)
    assert not window.undo_manager.can_undo()
