"""The Rename/Export dialog's "Move into folders" mode in the main window:
the library root is passed in and remembered, the planned moves run through
redactor_common's runner with the app's rename log (sidecar files travel
with their video), the rows take the new paths, File > Undo Last Rename puts
everything back, and Rename/Export behave as before. Dialog exec()s are
patched so the real handlers run headless.

Also the settings the move mode shares with Redact's steps."""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox  # noqa: E402
from redactor_common.core.pipeline import FileStatus, Recipe, run_recipe_on_item  # noqa: E402
from redactor_common.core.rename_log import RenameLog  # noqa: E402

from core import redact_steps as rs  # noqa: E402
from core.config import get_setting, set_setting  # noqa: E402
from core.video_file import VideoFile  # noqa: E402
from core.video_metadata import VideoMetadata  # noqa: E402

_app = QApplication.instance() or QApplication([])

MOVE_PATTERN = "%show_title%/Season %season_number%/%title%"


@pytest.fixture
def window(monkeypatch, tmp_path):
    import core.config as config
    import gui.main_window as mw

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")
    monkeypatch.setattr(mw.MainWindow, "_restore_last_folder_on_startup", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_check_external_tools_on_startup", lambda self: None)
    w = mw.MainWindow()
    incoming = tmp_path / "incoming"
    incoming.mkdir()
    files = []
    for n, title in ((1, "Pilot"), (2, "Diversity Day")):
        path = incoming / f"raw{n}.mp4"
        path.write_bytes(b"video")
        (incoming / f"raw{n}.en.srt").write_text("subs")
        (incoming / f"raw{n}-poster.jpg").write_bytes(b"jpg")
        files.append(VideoFile(path=path, metadata=VideoMetadata(show_title="The Office", season_number=1, title=title)))
    w.video_files = files
    w._refresh_table_rows()
    w.table.selectAll()
    yield w
    for vf in w.video_files:
        vf.dirty = False
    w.close()


def accept_in_move_mode(monkeypatch, library, pattern=MOVE_PATTERN, choose_root=True):
    import gui.main_window as mw

    seen = {}

    def fake_exec(self):
        seen["library_root"] = self.library_root()
        if choose_root:
            monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(library))
            self.move_radio.setChecked(True)
            self.choose_root_btn.click()
        else:
            self.move_radio.setChecked(True)
        self.pattern_edit.setText(pattern)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(mw.RenamePatternDialog, "exec", fake_exec)
    return seen


def test_move_mode_moves_videos_and_sidecars_updates_rows_and_remembers_the_root(window, monkeypatch, tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    seen = accept_in_move_mode(monkeypatch, library)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)  # tidy up
    window._on_rename_by_pattern()

    season = library / "The Office" / "Season 1"
    assert sorted(os.listdir(season)) == sorted([
        "Pilot.mp4", "Pilot.en.srt", "Pilot-poster.jpg",
        "Diversity Day.mp4", "Diversity Day.en.srt", "Diversity Day-poster.jpg",
    ])
    assert [vf.path for vf in window.video_files] == [season / "Pilot.mp4", season / "Diversity Day.mp4"]
    assert not (tmp_path / "incoming").exists()  # emptied, and the tidy-up said yes
    assert seen["library_root"] == ""  # nothing remembered yet...
    assert get_setting("rename", "library_root") == str(library)  # ...and now it is
    assert get_setting("rename", "move_pattern") == MOVE_PATTERN
    assert "Moved 2 of 2 file(s) into folders (+4 sidecar file(s))" in window.status_bar.currentMessage()


def test_the_remembered_root_is_passed_to_the_dialog_next_time(window, monkeypatch, tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    set_setting("rename", "library_root", str(library))
    seen = accept_in_move_mode(monkeypatch, library, choose_root=False)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    window._on_rename_by_pattern()
    assert seen["library_root"] == str(library)
    assert (library / "The Office" / "Season 1" / "Pilot.mp4").exists()
    assert (tmp_path / "incoming").exists()  # tidy-up declined


def test_undo_last_rename_puts_videos_and_sidecars_back_as_one_batch(window, monkeypatch, tmp_path):
    import gui.main_window as mw

    library = tmp_path / "library"
    library.mkdir()
    accept_in_move_mode(monkeypatch, library)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    originals = [vf.path for vf in window.video_files]
    window._on_rename_by_pattern()

    batch = mw._rename_log().last_batch()
    assert batch.label == "Move into Folders" and len(batch.renames) == 6 and batch.root == str(library)
    assert [os.path.basename(d) for d in batch.created_dirs] == ["The Office", "Season 1"]

    window.undo_last_rename()  # asks "Undo the last rename?" (patched to No above) ...
    assert (library / "The Office" / "Season 1" / "Pilot.mp4").exists()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    window.undo_last_rename()
    assert [vf.path for vf in window.video_files] == originals
    for vf in window.video_files:
        assert vf.path.exists() and vf.path.with_suffix(".en.srt").exists()
        assert (vf.path.parent / f"{vf.path.stem}-poster.jpg").exists()


def test_a_video_that_cannot_move_is_reported_and_the_others_still_move(window, monkeypatch, tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    accept_in_move_mode(monkeypatch, library)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text, *a, **k: warned.append(text))
    (library / "The Office" / "Season 1").mkdir(parents=True)
    # A file where raw2's sidecar should land (numbering only applies to the video itself).
    (library / "The Office" / "Season 1" / "Diversity Day.en.srt").write_text("taken")
    window._on_rename_by_pattern()
    assert (library / "The Office" / "Season 1" / "Pilot.mp4").exists()
    assert (library / "The Office" / "Season 1" / "Diversity Day.mp4").exists()
    assert len(warned) == 1 and "Diversity Day.en.srt" in warned[0]
    assert (library / "The Office" / "Season 1" / "Diversity Day.en.srt").read_text() == "taken"
    assert (tmp_path / "incoming" / "raw2.en.srt").exists()  # the one that could not move stays


def test_rename_mode_is_unchanged_and_never_touches_the_move_runner(window, monkeypatch, tmp_path):
    import gui.main_window as mw

    monkeypatch.setattr(mw, "run_planned_moves", lambda *a, **k: pytest.fail("moved in rename mode"))

    def rename_mode(self):
        self.pattern_edit.setText("%title%")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(mw.RenamePatternDialog, "exec", rename_mode)
    window._on_rename_by_pattern()
    assert [vf.path.name for vf in window.video_files] == ["Pilot.mp4", "Diversity Day.mp4"]
    assert (tmp_path / "incoming" / "Pilot.mp4").exists()
    assert get_setting("rename", "library_root") == "" and get_setting("rename", "move_pattern") == ""
    assert mw._rename_log().last_batch().label == "Rename by Pattern"


# --- what the move mode shares with Redact ---------------------------------------


def test_redact_moves_with_the_pattern_last_used_in_the_dialog(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    settings = {("rename", "library_root"): str(library), ("rename", "move_pattern"): "%show_title%/%title%"}
    env = rs.RedactEnv(setting=lambda s, k, d="": settings.get((s, k), d), rename_log=RenameLog(str(tmp_path / "log.json")))
    path = tmp_path / "a.mp4"
    path.write_bytes(b"")
    vf = VideoFile(path=path, metadata=VideoMetadata(show_title="Show", title="Pilot"))
    catalogue = rs.build_catalogue(lambda: [])
    recipe = Recipe.default_for(catalogue)
    recipe.enabled = {s.key: s.key == "move_into_folders" for s in catalogue}
    entry = run_recipe_on_item(vf, recipe.resolve(catalogue), 0.9, lambda v: rs.VideoCtx(v, env), lambda v: v.path.name)
    assert entry.status is FileStatus.CHANGED and vf.path == library / "Show" / "Pilot.mp4"


def test_a_move_pattern_in_the_shared_history_is_not_used_to_rename_or_parse(tmp_path):
    history = [MOVE_PATTERN, "%show_title% - %title%", "%title%"]
    assert rs.latest_file_pattern(history) == "%show_title% - %title%"
    assert rs.latest_file_pattern([MOVE_PATTERN, "a\\b"]) == ""
    # ... so Rename does not start enabled on the strength of a move pattern alone.
    assert Recipe.default_for(rs.build_catalogue(lambda: [MOVE_PATTERN])).enabled["rename"] is False

    path = tmp_path / "a.mp4"
    path.write_bytes(b"")
    vf = VideoFile(path=path, metadata=VideoMetadata(show_title="Show", title="Pilot"))
    env = rs.RedactEnv(pattern_history=lambda: history)
    catalogue = rs.build_catalogue(lambda: history)
    recipe = Recipe.default_for(catalogue)
    recipe.enabled = {s.key: s.key == "rename" for s in catalogue}
    run_recipe_on_item(vf, recipe.resolve(catalogue), 0.9, lambda v: rs.VideoCtx(v, env), lambda v: v.path.name)
    assert vf.path.name == "Show - Pilot.mp4"
