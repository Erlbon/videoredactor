"""Pin bump to redactor_common 2026-11-27-06: pywin32 and the spec's hidden imports (library card 64), the Play
window letting go of a file before it is saved or renamed (card 65), and the filename Search & Replace checking
the new name (card 99)."""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtWidgets import QMessageBox  # noqa: E402

from redactor_common.gui import media_preview  # noqa: E402
from tests.test_batch_operations_and_undo import _app, window  # noqa: E402,F401

ROOT = Path(__file__).resolve().parent.parent


def test_requirements_pin_and_pywin32():
    text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert 'pywin32>=306; sys_platform == "win32"' in text
    assert "redactor_common.git@2026-11-27-06" in text
    assert "redactor_common.git@2026-11-26-02" not in text


def test_spec_names_the_windows_recycle_bin_modules():
    spec = (ROOT / "videoredactor.spec").read_text(encoding="utf-8")
    for module in ("send2trash.win.modern", "win32com.shell.shell", "win32com.shell.shellcon",
                   "pythoncom", "pywintypes"):
        assert f'"{module}"' in spec
    assert 'sys.platform == "win32"' in spec


@pytest.fixture
def released(monkeypatch):
    calls = []
    monkeypatch.setattr(media_preview, "release_previews_of", lambda paths: calls.append([str(p) for p in paths]))
    return calls


def test_save_releases_the_play_window_first(window, released, monkeypatch):
    vf = window.video_files[0]
    monkeypatch.setattr(type(vf), "save", lambda self: None)
    window._save_files([vf])
    assert [str(vf.path)] in released


def test_single_rename_releases_the_play_window_first(window, released, monkeypatch):
    import gui.main_window as mw

    vf = window.video_files[0]
    monkeypatch.setattr(mw, "prompt_rename_single_file", lambda *a, **k: False)
    window.rename_single_file(vf)
    assert [str(vf.path)] in released


def _filename_replace(window, monkeypatch, changes):
    import gui.main_window as mw
    from redactor_common.gui.search_replace_dialog import FILENAME_FIELD_KEY

    dialog = mw.SearchReplaceDialog
    monkeypatch.setattr(dialog, "exec", lambda d: dialog.DialogCode.Accepted)
    monkeypatch.setattr(dialog, "result_field_key", lambda d: FILENAME_FIELD_KEY)
    monkeypatch.setattr(dialog, "accepted_changes", lambda d: changes)
    window._on_search_replace()


def test_filename_search_replace_releases_the_play_window_first(window, released, monkeypatch):
    first = window.video_files[0]
    old = str(first.path)
    _filename_replace(window, monkeypatch, {0: "Renamed"})
    assert [old] in released
    assert first.path.name == "Renamed.mkv"


@pytest.mark.parametrize("bad", ["sub/x", "CON", "name.", "name ", "a:b"])
def test_filename_search_replace_rejects_invalid_names(window, monkeypatch, bad):
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a[2])))
    first = window.video_files[0]
    folder = first.path.parent
    before_files = sorted(os.listdir(folder))
    before = first.path
    _filename_replace(window, monkeypatch, {0: bad})
    assert first.path == before and before.exists()
    assert sorted(os.listdir(folder)) == before_files
    assert warned, bad


def test_a_blank_content_type_is_not_an_existing_value_to_overwrite():
    from core.video_metadata import ContentType, VideoMetadata
    from redactor_common.gui.overwrite_review_dialog import _text

    assert _text(VideoMetadata().content_type) == ""
    assert _text(ContentType.PORN) == "Porn" and str(ContentType.MOVIE) == "Movie"
