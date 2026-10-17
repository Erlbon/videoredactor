"""Save All reports its result after EVERY run (status line with counts or the reason nothing was saved, a box listing
each problem file, an unexpected error in the "Something went wrong" box), never stops at the first bad file (a step
that raises used to abort run_with_progress's whole loop), and names a file left marked unsaved without an error.
Real MainWindow offscreen, real generated MP4s."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402
from redactor_common.gui import crash_dialog  # noqa: E402

from core import crash_log, history  # noqa: E402
from core.video_file import VideoFile  # noqa: E402
from tests.test_clear_fields import needs_ffmpeg  # noqa: E402
from tests.test_per_file_confirmation import close, make_window  # noqa: E402

_app = QApplication.instance() or QApplication([])
NAMES = ["One.mp4", "Two.mp4", "Three.mp4"]


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(crash_log, "log_path", lambda: str(tmp_path / "logs" / "videoredactor_crash.log"))
    monkeypatch.setattr(crash_dialog, "_reporter", None)


@pytest.fixture
def reporter(tmp_path):
    rep = crash_dialog.install(tmp_path / "logs" / "videoredactor_crash.log")
    yield rep
    if rep.dialog is not None:
        rep.dialog.close()
    _app.processEvents()


@pytest.fixture
def boxes(monkeypatch):
    seen = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda parent, title, text, *a: seen.append((title, text))))
    return seen


def _dirty(vf, title):
    vf.metadata.title = title
    vf.dirty = True


def _title_on_disk(vf):
    from mutagen.mp4 import MP4

    tags = MP4(str(vf.path)).tags or {}
    return (tags.get("\xa9nam") or [""])[0]


@needs_ffmpeg
def test_all_clean_says_why_nothing_was_saved(monkeypatch, tmp_path):
    w = make_window(monkeypatch, tmp_path, NAMES, real=True)
    w._on_save_all()
    assert w.status_bar.currentMessage() == "Nothing to save: no file has unsaved changes"
    close(w)


@needs_ffmpeg
def test_all_changed_files_with_load_errors_name_them(monkeypatch, tmp_path):
    w = make_window(monkeypatch, tmp_path, NAMES, real=True)
    for vf in w.video_files[:2]:
        vf.dirty = True
        vf.load_error = "broken"
    w._on_save_all()
    message = w.status_bar.currentMessage()
    assert message.startswith("Nothing to save: all 2 changed file(s) have load errors")
    assert "One.mp4" in message and "Two.mp4" in message
    close(w)


@needs_ffmpeg
def test_a_failing_file_is_listed_in_the_box_and_the_others_are_saved(monkeypatch, tmp_path, boxes):
    w = make_window(monkeypatch, tmp_path, NAMES, real=True)
    one, two, three = w.video_files
    before_two = _title_on_disk(two)
    for vf in (one, two, three):
        _dirty(vf, "New " + vf.path.stem)
    real = VideoFile.save

    def flaky(self, *a, **k):
        if self.path.name == "Two.mp4":
            self.save_error = "disk says no"
            return None
        return real(self, *a, **k)

    monkeypatch.setattr(VideoFile, "save", flaky)
    w._on_save_all()
    assert w.status_bar.currentMessage() == "Saved 2 file(s), 1 failed"
    assert len(boxes) == 1 and "Two.mp4: disk says no" in boxes[0][1]
    assert _title_on_disk(one) == "New One" and _title_on_disk(three) == "New Three"
    assert _title_on_disk(two) == before_two
    close(w)


@needs_ffmpeg
def test_an_exception_in_the_middle_shows_the_box_and_the_loop_goes_on(monkeypatch, tmp_path, boxes, reporter):
    """The suspected cause of 'Save All sometimes does nothing': an exception from one file's save ended
    run_with_progress's whole loop silently."""
    w = make_window(monkeypatch, tmp_path, NAMES, real=True)
    one, two, three = w.video_files
    for vf in (one, two, three):
        _dirty(vf, "New " + vf.path.stem)
    real = VideoFile.save

    def explode(self, *a, **k):
        if self.path.name == "Two.mp4":
            raise ValueError("kaboom")
        return real(self, *a, **k)

    monkeypatch.setattr(VideoFile, "save", explode)
    w._on_save_all()
    _app.processEvents()
    assert reporter.dialog is not None and "kaboom" in reporter.dialog.details_text()
    assert _title_on_disk(one) == "New One" and _title_on_disk(three) == "New Three"  # Three still saved
    assert w.status_bar.currentMessage() == "Saved 2 file(s), 1 failed"
    assert "Two.mp4: unexpected error: kaboom" in boxes[0][1]
    assert two.save_error == "unexpected error: kaboom" and two.dirty
    close(w)


@needs_ffmpeg
def test_an_exception_outside_the_per_file_loop_is_reported_and_shown_in_the_status_bar(monkeypatch, tmp_path, reporter):
    w = make_window(monkeypatch, tmp_path, NAMES, real=True)
    _dirty(w.video_files[0], "x")

    def boom(*a, **k):
        raise RuntimeError("no way")

    monkeypatch.setattr(w, "_save_files", boom)
    w._on_save_all()
    _app.processEvents()
    assert reporter.dialog is not None
    assert reporter.dialog.summary_label.text().startswith("Save All stopped because of an unexpected error")
    assert "no way" in w.status_bar.currentMessage()
    close(w)


@needs_ffmpeg
def test_the_invariant_names_a_file_left_unsaved_without_an_error(monkeypatch, tmp_path, boxes):
    w = make_window(monkeypatch, tmp_path, NAMES, real=True)
    one = w.video_files[0]
    _dirty(one, "x")
    monkeypatch.setattr(VideoFile, "save", lambda self, *a, **k: None)  # "saves" but leaves it dirty, no error
    w._on_save_all()
    assert "still marked unsaved" in w.status_bar.currentMessage()
    assert boxes and "1 file(s) are still marked unsaved after saving: One.mp4" in boxes[0][1]
    close(w)


@needs_ffmpeg
def test_the_save_log_is_opt_in_and_has_no_file_names(monkeypatch, tmp_path):
    w = make_window(monkeypatch, tmp_path, NAMES, real=True)
    log = tmp_path / "logs" / "save_log.txt"
    _dirty(w.video_files[0], "x")
    w._on_save_all()
    assert not log.exists()  # data logging is off by default
    history.set_history_enabled(True)
    _dirty(w.video_files[1], "y")
    w._on_save_all()
    text = log.read_text(encoding="utf-8")
    assert text.count("\n") == 1 and "saved=1" in text and "Two.mp4" not in text
    close(w)
