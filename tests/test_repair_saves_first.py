"""Media > Check Files / Repair no longer skips files with unsaved edits: they are saved FIRST (same path as Save All),
then repaired. A file whose save fails is not repaired and is reported with its save error; clean files behave as
before; the per-file guard in the worker stays as a safety net. Real MainWindow offscreen, real generated MP4s."""

import os
import shutil

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtWidgets import QDialog, QMessageBox  # noqa: E402

import core.file_check as fc  # noqa: E402
from core.video_file import VideoFile  # noqa: E402
from gui import file_check_dialog as flow  # noqa: E402
from tests.test_file_check import qapp, videos  # noqa: E402,F401


@pytest.fixture
def window(monkeypatch, tmp_path, qapp):
    import core.config as config
    import gui.main_window as mw

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")
    monkeypatch.setattr(mw.MainWindow, "_restore_last_folder_on_startup", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_check_external_tools_on_startup", lambda self: None)
    monkeypatch.setattr("core.file_check.move_to_trash", lambda _p: None)
    # repair() took move_to_trash as a default argument at import time:
    monkeypatch.setattr(fc.repair, "__defaults__", (fc._run, lambda _p: None, fc.restore_app_metadata))
    w = mw.MainWindow()
    yield w
    for vf in w.video_files:
        vf.dirty = False
    w.close()


def load(videos, tmp_path, window, names):
    files = []
    for name in names:
        vf = VideoFile(path=tmp_path / name)
        shutil.copyfile(videos / "late_index.mp4", vf.path)
        vf.load()
        vf.check = fc.quick_check(str(vf.path))
        assert any(f.code == "index_at_end" for f in vf.check.findings)  # repairable: the index sits at the end
        files.append(vf)
    window.video_files = files
    window._refresh_table_rows()
    return files


def stage(vf, title):
    vf.metadata.title = title
    vf.dirty = True


def title_on_disk(vf):
    from mutagen.mp4 import MP4

    tags = MP4(str(vf.path)).tags or {}
    return (tags.get("\xa9nam") or [""])[0]


def repaired_layout(vf):
    return not any(f.code == "index_at_end" for f in fc.quick_check(str(vf.path)).findings)


def test_a_dirty_file_is_saved_then_repaired_and_reads_back_with_both(window, videos, tmp_path):
    one, two, three = load(videos, tmp_path, window, ["a.mp4", "b.mp4", "c.mp4"])
    stage(two, "Staged Title")
    repaired, errors, notes = flow.repair_files(window, [one, two, three])
    assert errors == [] and len(repaired) == 3
    assert not flow.unsaved([two]) and not two.save_error  # (the repair's own check stamp may still be pending)
    assert title_on_disk(two) == "Staged Title"  # the staged metadata is in the repaired file
    assert all(repaired_layout(vf) for vf in (one, two, three))  # and the layout is repaired
    assert notes[0] == "Saved 1 file(s) with unsaved changes first, then repaired them."


def test_a_failing_save_blocks_only_that_files_repair(window, videos, tmp_path, monkeypatch):
    one, two, three = load(videos, tmp_path, window, ["a.mp4", "b.mp4", "c.mp4"])
    stage(one, "Staged One")
    stage(two, "Staged Two")
    real_save = VideoFile.save

    def flaky(self, *args, **kwargs):
        if self.path.name == "b.mp4":
            self.save_error = "disk says no"
            return None
        return real_save(self, *args, **kwargs)

    monkeypatch.setattr(VideoFile, "save", flaky)
    boxes = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda parent, title, text, *a: boxes.append((title, text))))
    repaired, errors, notes = flow.repair_files(window, [one, two, three])
    assert {vf.path.name for vf in repaired} == {"a.mp4", "c.mp4"}
    assert repaired_layout(one) and repaired_layout(three) and not repaired_layout(two)  # b.mp4 left alone
    assert title_on_disk(one) == "Staged One"
    assert len(errors) == 1 and errors[0].startswith("b.mp4: not repaired -- saving its unsaved changes first failed")
    assert "disk says no" in errors[0]
    assert boxes and boxes[0][0] == "Some files failed to save"  # Save All's own failure box
    assert notes[0] == "Saved 1 file(s) with unsaved changes first, then repaired them."


def test_clean_files_behave_as_before_and_nothing_is_saved(window, videos, tmp_path, monkeypatch):
    one, two = load(videos, tmp_path, window, ["a.mp4", "b.mp4"])
    saves = []
    monkeypatch.setattr(VideoFile, "save", lambda self, *a, **k: saves.append(self.path.name))
    repaired, errors, notes = flow.repair_files(window, [one, two])
    assert len(repaired) == 2 and errors == [] and saves == []
    assert not any("unsaved" in note for note in notes)


def test_a_stamp_only_change_needs_no_save(window, videos, tmp_path, monkeypatch):
    (one,) = load(videos, tmp_path, window, ["a.mp4"])
    one.dirty = True
    one.stamp_only_dirty = True
    saves = []
    monkeypatch.setattr(VideoFile, "save", lambda self, *a, **k: saves.append(self.path.name))
    repaired, errors, _notes = flow.repair_files(window, [one])
    assert len(repaired) == 1 and errors == [] and saves == []


def test_the_worker_guard_is_still_a_safety_net(videos, tmp_path, qapp):
    # No window to save through: a file with unsaved edits is still refused, as before.
    vf = VideoFile(path=tmp_path / "late_index.mp4")
    shutil.copyfile(videos / "late_index.mp4", vf.path)
    vf.load()
    vf.check = fc.quick_check(str(vf.path))
    vf.dirty = True
    repaired, errors, _notes = flow.repair_files(None, [vf])
    assert not repaired and "unsaved changes" in errors[0]


def test_the_whole_flow_says_it_plainly_in_the_status_bar(window, videos, tmp_path, monkeypatch):
    one, two = load(videos, tmp_path, window, ["a.mp4", "b.mp4"])
    stage(one, "Staged One")
    monkeypatch.setattr(flow.FileCheckResultsDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(flow.QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    infos = []
    monkeypatch.setattr(flow.QMessageBox, "information", lambda parent, title, text, *a, **k: infos.append(text))
    monkeypatch.setattr(flow.QMessageBox, "warning", lambda *a, **k: None)
    monkeypatch.setattr(flow, "check_files", lambda w, files, errors=None, deep=False: files)
    flow.run_check_and_repair(window, [one, two], lambda lines: "\n".join(lines), deep=False)
    message = window.status_bar.currentMessage()
    assert message == "Saved 1 file(s) with unsaved changes first, then repaired 2 of 2 file(s)"
    assert infos and "Saved 1 file(s) with unsaved changes first, then repaired them." in infos[0]
    assert title_on_disk(one) == "Staged One"
