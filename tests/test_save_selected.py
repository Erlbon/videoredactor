"""Right-click > Save Selected (N): saves only the selected files that have unsaved changes, through the same
path as File > Save All (progress, journal, failure box, status text); greyed when none of the selection is
changed; a failing file does not block the others. Real MainWindow offscreen, real generated MP4s."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint  # noqa: E402
from PyQt6.QtWidgets import QMenu, QMessageBox  # noqa: E402

from core.video_file import VideoFile  # noqa: E402
from tests.test_clear_fields import needs_ffmpeg, select  # noqa: E402
from tests.test_per_file_confirmation import close, make_window  # noqa: E402

NAMES = ["One.mp4", "Two.mp4", "Three.mp4"]


def _dirty(vf, title):
    vf.metadata.title = title
    vf.dirty = True


def _title_on_disk(vf):
    from mutagen.mp4 import MP4
    tags = MP4(str(vf.path)).tags or {}
    return (tags.get("\xa9nam") or [""])[0]


def _open_menu(window, files):
    select(window, *files)
    row = next(r for r in range(window.table.rowCount()) if window._file_for_row(r) is files[0])
    shown = []
    original = QMenu.exec
    QMenu.exec = lambda self, *a: shown.extend(self.actions())
    try:
        window._show_table_context_menu(QPoint(5, window.table.rowViewportPosition(row) + 5))
    finally:
        QMenu.exec = original
    return shown


def _save_action(actions):
    return next(a for a in actions if a.text().startswith("Save Selected"))


@needs_ffmpeg
def test_count_counts_only_changed_selected_files(monkeypatch, tmp_path):
    w = make_window(monkeypatch, tmp_path, NAMES, real=True)
    one, two, three = w.video_files
    _dirty(one, "New One")
    _dirty(three, "New Three")
    action = _save_action(_open_menu(w, [one, two]))
    assert action.text() == "Save Selected (1)" and action.isEnabled()
    close(w)


@needs_ffmpeg
def test_menu_item_saves_only_the_selected_changed_files(monkeypatch, tmp_path):
    w = make_window(monkeypatch, tmp_path, NAMES, real=True)
    one, two, three = w.video_files
    before_three = _title_on_disk(three)
    for vf, title in ((one, "New One"), (two, "New Two"), (three, "New Three")):
        _dirty(vf, title)
    _save_action(_open_menu(w, [one, two])).trigger()
    assert _title_on_disk(one) == "New One" and _title_on_disk(two) == "New Two"
    assert _title_on_disk(three) == before_three  # not selected: untouched on disk
    assert not one.dirty and not two.dirty
    assert three.dirty  # and still changed in the list
    assert w.status_bar.currentMessage().startswith("Saved 2 file(s)")
    close(w)


@needs_ffmpeg
def test_greyed_when_nothing_selected_is_changed(monkeypatch, tmp_path):
    w = make_window(monkeypatch, tmp_path, NAMES, real=True)
    one, two, three = w.video_files
    _dirty(three, "New Three")
    action = _save_action(_open_menu(w, [one, two]))
    assert action.text() == "Save Selected (0)" and not action.isEnabled()
    assert action.toolTip() == "The selected files have no unsaved changes"
    close(w)


@needs_ffmpeg
def test_a_pending_panel_edit_counts_and_is_saved(monkeypatch, tmp_path):
    w = make_window(monkeypatch, tmp_path, NAMES, real=True)
    one, two, three = w.video_files
    select(w, one)
    applied = []
    monkeypatch.setattr(w.tag_panel, "apply_pending_changes", lambda: applied.append(True))
    w._on_save_selected([one])  # nothing dirty: nothing to write, but the panel flush ran like Save All's
    assert applied
    close(w)


@needs_ffmpeg
def test_a_failing_file_does_not_block_the_others_and_shows_the_failure_box(monkeypatch, tmp_path):
    w = make_window(monkeypatch, tmp_path, NAMES, real=True)
    one, two, three = w.video_files
    before_two = _title_on_disk(two)
    for vf in (one, two, three):
        _dirty(vf, "New " + vf.path.stem)
    real_save = VideoFile.save

    def flaky(self, *args, **kwargs):
        if self.path.name == "Two.mp4":
            self.save_error = "disk says no"
            return None
        return real_save(self, *args, **kwargs)

    monkeypatch.setattr(VideoFile, "save", flaky)
    boxes = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda parent, title, text, *x: boxes.append((title, text))))
    w._on_save_selected([one, two, three])
    assert _title_on_disk(one) == "New One" and _title_on_disk(three) == "New Three"
    assert _title_on_disk(two) == before_two
    assert two.dirty and two.save_error
    assert len(boxes) == 1 and boxes[0][0] == "Some files failed to save"
    assert "Two.mp4" in boxes[0][1] and "disk says no" in boxes[0][1]
    assert w.status_bar.currentMessage().startswith("Saved 2 file(s), 1 failed")
    close(w)
