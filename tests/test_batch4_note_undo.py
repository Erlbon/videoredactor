"""Batch 4 (2026-10-17): a written note (created or refreshed) is undone from Tools > History through the real dialog,
the real journal and the real notes flow. The Recycle Bin is never touched: move_to_trash is replaced by a fake that moves
the file into a folder inside tmp_path."""

import os
import shutil
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox

from core import history
from core.notes_flow import build_proposals, scan, undo_note_entry, write_proposal
from redactor_common.core import trash as trash_module
from redactor_common.core.notes import NoteFrontMatter, note_status, render_note
from redactor_common.gui.history_dialog import HistoryDialog
from tests.notes_helpers import fake_fetchers, make_series

_app = QApplication.instance() or QApplication([])


@pytest.fixture
def fake_bin(tmp_path, monkeypatch):
    folder = tmp_path / "fake-recycle-bin"
    folder.mkdir()
    moved = []

    def move_to_trash(path, fallback_folder=None):
        moved.append(path)
        shutil.move(path, str(folder / f"{len(moved)}-{os.path.basename(path)}"))

    monkeypatch.setattr(trash_module, "move_to_trash", move_to_trash)
    monkeypatch.setattr("send2trash.send2trash", lambda p: pytest.fail("the real Recycle Bin was touched"), raising=False)
    return moved


def _dialog():
    return HistoryDialog(history.get_journal(), lambda path, values: "", undo_handlers=history.note_undo_handlers())


def _yes(monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(a[2])))
    return warnings


def _old_note(show):
    old = render_note(NoteFrontMatter(title="Breaking Bad", generator="videoredactor 2026-01-01#01", note_format=1),
                      "# Breaking Bad\n\nOld text.")
    (show / "series.md").write_bytes(old.encode("utf-8"))
    return old


def test_a_created_note_is_undone_from_the_history_dialog(tmp_path, fake_bin, monkeypatch):
    history.set_history_enabled(True)
    show = make_series(tmp_path)
    [proposal] = build_proposals(scan([tmp_path]).targets, fake_fetchers())
    path = write_proposal(proposal).path
    dialog = _dialog()
    dialog.table.selectRow(0)
    assert dialog.restore_button.isEnabled()  # it was disabled for every note entry
    _yes(monkeypatch)
    assert dialog.restore_selected() is True
    assert not (show / "series.md").exists() and fake_bin == [path]
    assert [e.status for e in history.get_journal().entries()] == ["restored"]


def test_a_refreshed_note_is_undone_and_the_older_text_is_back(tmp_path, fake_bin, monkeypatch):
    history.set_history_enabled(True)
    show = make_series(tmp_path)
    old = _old_note(show)
    [update] = build_proposals(scan([tmp_path], include_outdated=True).targets, fake_fetchers())
    assert write_proposal(update).ok
    [entry] = history.get_journal().entries()
    assert entry.kind == "note-update"
    backup = Path(history.notes_backup_base()) / "notes_backup" / f"{entry.id}.md"
    assert backup.read_bytes() == old.encode("utf-8")
    assert not str(backup).startswith(str(tmp_path / "Breaking Bad (2008)"))  # never inside a library folder
    dialog = _dialog()
    dialog.table.selectRow(0)
    _yes(monkeypatch)
    assert dialog.restore_selected() is True
    assert (show / "series.md").read_bytes() == old.encode("utf-8")
    assert note_status(str(show / "series.md")).kind == "outdated-untouched"
    assert not backup.exists()


def test_with_history_off_a_refresh_stores_nothing(tmp_path, fake_bin):
    show = make_series(tmp_path)
    _old_note(show)
    [update] = build_proposals(scan([tmp_path], include_outdated=True).targets, fake_fetchers())
    assert write_proposal(update).ok
    assert not (Path(history.notes_backup_base()) / "notes_backup").exists()
    assert not os.path.exists(history.get_journal().path)


def test_an_edited_note_is_not_undone_and_stays(tmp_path, fake_bin):
    history.set_history_enabled(True)
    show = make_series(tmp_path)
    [proposal] = build_proposals(scan([tmp_path]).targets, fake_fetchers())
    write_proposal(proposal)
    (show / "series.md").write_bytes(b"my own words\n")
    [entry] = history.get_journal().entries()
    assert "changed" in undo_note_entry(entry)
    assert (show / "series.md").exists() and fake_bin == []


def test_a_refresh_on_a_network_drive_is_refused_without_a_backup_folder_and_works_with_one(tmp_path, monkeypatch):
    from core.library_safety import SECTION
    from core import config

    show = make_series(tmp_path)
    old = _old_note(show)
    monkeypatch.setattr(trash_module, "trash_is_reliable", lambda p, drive_type=None: False)  # a fake UNC share
    monkeypatch.setattr("send2trash.send2trash", lambda p: pytest.fail("the real Recycle Bin was touched"), raising=False)
    [update] = build_proposals(scan([tmp_path], include_outdated=True).targets, fake_fetchers())
    refused = write_proposal(update)
    assert not refused.ok and "network drive" in refused.message
    assert (show / "series.md").read_bytes() == old.encode("utf-8")
    config.set_setting(SECTION, "quarantine_root", str(tmp_path / "quarantine"))
    assert write_proposal(update).ok
    assert list((tmp_path / "quarantine").rglob("series.md"))


def test_the_poster_migration_dialog_says_the_poster_is_scaled_and_the_original_goes_to_the_bin():
    from PyQt6.QtWidgets import QLabel

    from gui.embed_posters_dialog import EmbedPostersDialog

    dialog = EmbedPostersDialog([])
    texts = " ".join(label.text() for label in dialog.findChildren(QLabel))
    assert "The poster is scaled to at most 1000 px and 400 KB; the original file goes to the Recycle Bin." in texts
