"""Open Folder tells about video files Import & Convert could bring in, and offers to convert them."""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QCheckBox, QMessageBox  # noqa: E402

from core.video_file import discover_convertible_files  # noqa: E402

_app = QApplication.instance() or QApplication([])


def _tree(root: Path) -> None:
    for name in ("a.mkv", "b.avi", "sub/c.WMV", "sub/deep/d.mov", "movie.vob", "stream.ts", "a.redact-part.avi", "notes.txt"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")


def test_finds_convertible_videos_but_not_disc_images_ts_or_temp_files(tmp_path):
    _tree(tmp_path)
    rel = lambda ps: [p.relative_to(tmp_path).as_posix() for p in ps]  # noqa: E731
    assert rel(discover_convertible_files(tmp_path, recursive=True)) == ["b.avi", "sub/c.WMV", "sub/deep/d.mov"]
    assert rel(discover_convertible_files(tmp_path, recursive=False)) == ["b.avi"]
    assert discover_convertible_files(tmp_path / "missing") == []


@pytest.fixture
def window(monkeypatch, tmp_path):
    import core.config as config
    import gui.main_window as mw

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")
    monkeypatch.setattr(mw.MainWindow, "_restore_last_folder_on_startup", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_check_external_tools_on_startup", lambda self: None)
    w = mw.MainWindow()
    yield w
    w.close()


def _dry_box(box):
    """The offer's "Dry run first" checkbox (an `options` widget of redactor_common's convert offer)."""
    found = [c for c in box.findChildren(QCheckBox) if c.text().startswith("Dry run first")]
    return found[0] if found else None


def _answer(box, text):
    """Click the button called `text` the way a user would (the shared offer reads clickedButton())."""
    for button in box.buttons():
        if button.text() == text:
            button.click()
    return QMessageBox.StandardButton.Yes if text == "Yes" else QMessageBox.StandardButton.No


def test_open_folder_offers_conversion_and_hands_the_files_over(window, monkeypatch, tmp_path):
    folder = tmp_path / "lib"
    _tree(folder)
    seen = {}
    asked = []

    def fake_exec(self):
        asked.append((self.text(), _dry_box(self)))
        _dry_box(self).setChecked(False)  # the user unticks "Dry run first" in the ONE dialog
        return _answer(self, "Yes")

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    monkeypatch.setattr(window, "_convert_paths", lambda paths, dry_run=None: seen.update(paths=paths, dry_run=dry_run))
    monkeypatch.setattr("gui.main_window.discover_video_files", lambda f, recursive, ignored=None: [])
    window._load_folder(folder, recursive=True)
    assert [os.path.basename(p) for p in seen["paths"]] == ["b.avi", "c.WMV", "d.mov"]
    assert ("No MP4 or MKV files found, but 3 video file(s) in other formats (AVI, MOV, WMV) that can be converted "
            "to MP4/MKV.\n\nConvert them now with the conversion rules?") in asked[0][0]
    assert len(asked) == 1 and asked[0][1] is not None  # the offer carries the dry-run checkbox itself
    assert seen["dry_run"] is False  # handed over, so _convert_paths asks nothing more


def test_open_folder_offer_defaults_to_a_dry_run_with_no_second_dialog(window, monkeypatch, tmp_path):
    import gui.main_window as mw

    folder = tmp_path / "lib"
    _tree(folder)
    boxes = []

    def fake_exec(self):
        boxes.append((self.windowTitle(), _dry_box(self).isChecked() if _dry_box(self) else None))
        return _answer(self, "Yes")

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    monkeypatch.setattr("gui.main_window.discover_video_files", lambda f, recursive, ignored=None: [])
    ran = []
    monkeypatch.setattr(mw.conversion_run, "process_file", lambda run, p, conv, **kw: ran.append(run.dry_run) or mw.conversion_run.ConversionOutcome(False, message="nothing to do"))
    window._load_folder(folder, recursive=True)
    assert boxes[0] == ("Other Video Formats Found", True)  # dry run on by default
    assert ran == [True, True, True]
    assert [title for title, _ in boxes] == ["Other Video Formats Found", "Dry run"]  # offer, then the report


def test_declining_converts_nothing_and_no_prompt_on_the_launch_restore(window, monkeypatch, tmp_path):
    folder = tmp_path / "lib"
    _tree(folder)
    calls = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: _answer(self, "No"))
    monkeypatch.setattr(window, "_convert_paths", lambda paths, dry_run=None: calls.append(paths))
    window._load_folder(folder, recursive=True)
    assert calls == []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: pytest.fail("no prompt when restoring the last folder at launch"))
    window._load_folder(folder, recursive=True, status_suffix=" (restored from last session)")


def test_include_subfolders_is_the_shared_checkbox_remembered_in_the_ini(window, monkeypatch, tmp_path):
    import gui.main_window as mw

    assert mw._load_include_subfolders() is True  # on by default
    mw._save_include_subfolders(False)
    assert mw._load_include_subfolders() is False
    # Open Folder asks no question and follows the setting
    seen = {}
    monkeypatch.setattr(window, "_load_folder", lambda folder, recursive, status_suffix="": seen.update(recursive=recursive))
    monkeypatch.setattr("gui.main_window.QFileDialog.getExistingDirectory", lambda *a, **k: str(tmp_path))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: pytest.fail("no subfolder question any more"))
    window._on_open_folder()
    assert seen["recursive"] is False
    mw._save_include_subfolders(True)
    window._on_open_folder()
    assert seen["recursive"] is True
