"""Redact in the main window: menu/toolbar entries, the targets question,
running the saved recipe through the shared engine, the results dialog,
Undo cleared afterwards, and the recipe editor saving to the settings.
Dialog exec()s are patched so the real handlers run headless; the
Recycle Bin is a fake folder."""

import os
import shutil
import subprocess
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox, QToolBar  # noqa: E402
from redactor_common.core.pipeline import Recipe  # noqa: E402

from core import redact_steps as rs  # noqa: E402
from core.mp4_backend import read_mp4_metadata  # noqa: E402
from core.video_file import VideoFile  # noqa: E402
from core.video_metadata import VideoMetadata  # noqa: E402

_app = QApplication.instance() or QApplication([])

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg/ffprobe not installed"
)


@pytest.fixture
def window(monkeypatch, tmp_path):
    import core.config as config
    import gui.main_window as mw

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")
    monkeypatch.setattr(mw.MainWindow, "_restore_last_folder_on_startup", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_check_external_tools_on_startup", lambda self: None)
    w = mw.MainWindow()
    yield w
    for vf in w.video_files:
        vf.dirty = False
    w.close()


def fake_bin(monkeypatch, tmp_path):
    folder = tmp_path / "bin"
    folder.mkdir()

    def trash(path):
        shutil.move(path, str(folder / f"{len(os.listdir(folder)) + 1}-{os.path.basename(path)}"))

    monkeypatch.setattr(rs, "move_to_trash", trash)
    return folder


def capture_results(monkeypatch):
    import gui.main_window as mw

    shown = {}

    def fake_exec(self):
        shown["header"] = self.header_label.text() if self.header_label else ""
        shown["report"] = self.report
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(mw.RedactResultsDialog, "exec", fake_exec)
    return shown


def stub_files(window, tmp_path, names):
    files = []
    for name in names:
        path = tmp_path / name
        path.write_bytes(b"")
        files.append(VideoFile(path=path, metadata=VideoMetadata()))
    window.video_files = files
    window._refresh_table_rows()
    return files


def test_redact_is_in_the_edit_menu_and_the_toolbar_with_the_family_shortcut(window):
    assert window.redact_action.shortcut().toString() == "Ctrl+Shift+E"
    toolbar = window.findChildren(QToolBar)[0]
    assert window.redact_action in toolbar.actions()
    menus = {m.text().replace("&", ""): m.menu() for m in window.menuBar().actions() if m.menu()}
    edit = [a.text().replace("&", "") for a in menus["Edit"].actions()]
    assert "Redact" in edit and "Edit Redact Recipe…" in edit


def test_nothing_loaded_says_so_and_nothing_selected_asks_first(window, monkeypatch, tmp_path):
    asked = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: asked.append(("info", a[1])))
    window._on_redact()
    assert asked == [("info", "Redact")]

    stub_files(window, tmp_path, ["a.mp4", "b.mp4"])
    questions = []

    def decline(parent, title, text, *a, **k):
        questions.append((title, text))
        return QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(QMessageBox, "question", decline)
    monkeypatch.setattr("gui.main_window.run_redact_dialog", lambda *a, **k: pytest.fail("ran after Cancel"))
    window._on_redact()
    assert questions[0][0] == "Redact all files?" and "all 2 loaded" in questions[0][1]


def test_unsaved_files_are_flagged_before_the_run(window, monkeypatch, tmp_path):
    files = stub_files(window, tmp_path, ["a.mp4", "b.mp4"])
    files[0].dirty = True
    window.table.selectAll()
    questions = []

    def decline(parent, title, text, *a, **k):
        questions.append(title)
        return QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(QMessageBox, "question", decline)
    monkeypatch.setattr("gui.main_window.run_redact_dialog", lambda *a, **k: pytest.fail("ran after Cancel"))
    window._on_redact()
    assert questions == ["Unsaved changes"]


def test_a_run_reports_skips_clears_undo_and_shows_the_bin_header(window, monkeypatch, tmp_path):
    files = stub_files(window, tmp_path, ["a.mp4", "b.mp4"])
    files[1].load_error = "broken"
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    shown = capture_results(monkeypatch)
    fake_bin(monkeypatch, tmp_path)
    window._push_undo("Something", files)
    assert window.undo_manager.can_undo()
    # A recipe that touches no network and needs no tool: rename only, with a pattern nobody filled.
    catalogue = rs.build_catalogue()
    recipe = Recipe.default_for(catalogue)
    recipe.enabled = {s.key: s.key == "filename_tags" for s in catalogue}
    rs.save_recipe(recipe)
    window._on_redact()
    report = shown["report"]
    assert [e.file for e in report.skipped()] == ["b.mp4"]
    assert "Recycle Bin" in shown["header"]
    assert not window.undo_manager.can_undo() and not window.undo_action.isEnabled()


@needs_ffmpeg
def test_redact_saves_a_real_file_in_place_and_refreshes_the_row(window, monkeypatch, tmp_path):
    src = tmp_path / "The Office S02E05 Halloween.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=duration=3:size=160x120:rate=10",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-c:v", "libx264", "-preset", "ultrafast",
        "-c:a", "aac", "-shortest", "-movflags", "+faststart", str(src),
    ], check=True, capture_output=True)
    vf = VideoFile(path=src)
    vf.load()
    window.video_files = [vf]
    window._refresh_table_rows()
    window.table.selectAll()
    bin_dir = fake_bin(monkeypatch, tmp_path)
    shown = capture_results(monkeypatch)
    from core.filename_pattern import save_pattern_to_history

    save_pattern_to_history("%show_title% S%season_number%E%episode_number% %title%")
    catalogue = rs.build_catalogue()
    recipe = Recipe.default_for(catalogue)
    recipe.enabled["lookup"] = False  # no network in tests
    rs.save_recipe(recipe)
    window._on_redact()
    md = read_mp4_metadata(str(src))
    assert (md.show_title, md.season_number, md.title) == ("The Office", 2, "Halloween")
    assert vf.metadata.show_title == "The Office" and not vf.dirty
    assert len(os.listdir(bin_dir)) == 1
    assert shown["report"].entries[0].status.value == "changed"
    assert window.table.rowCount() == 1


def test_the_recipe_editor_saves_the_edited_recipe_to_the_settings(window, monkeypatch):
    import gui.main_window as mw

    def accept_with_subtitles_on(self):
        for row in range(self.list.count()):
            if self.list.item(row).data(Qt.ItemDataRole.UserRole) == "subtitles":
                self.list.item(row).setCheckState(Qt.CheckState.Checked)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(mw.RecipeEditorDialog, "exec", accept_with_subtitles_on)
    window._on_edit_redact_recipe()
    loaded = rs.load_recipe(rs.build_catalogue())
    assert loaded.enabled["subtitles"] is True and loaded.enabled["check_repair"] is True
    assert loaded.order[-2:] == ["rename", "move_into_folders"]
