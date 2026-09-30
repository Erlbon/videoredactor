"""Tests for core/file_check.py (Operations > Check Files...) and its
dialog -- against real small videos generated with ffmpeg (as
tests/test_ffmpeg_backend.py does; CI installs ffmpeg): an intact file,
an MKV written without a seek index, an MP4 with its index at the end,
truncated copies, and two audio tracks with no default."""

import os
import shutil
import subprocess
import sys

import pytest

from core import file_check as fc

SOURCES = [
    "-f", "lavfi", "-i", "testsrc=duration=12:size=160x120:rate=10",
    "-f", "lavfi", "-i", "sine=frequency=440:duration=12",
]
ENCODE = ["-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest"]


def _ffmpeg(*args, stdout=None):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True, stdout=stdout, capture_output=stdout is None)


def _truncate(src, dest, fraction=0.55):
    data = open(src, "rb").read()
    open(dest, "wb").write(data[: int(len(data) * fraction)])


@pytest.fixture(scope="module")
def videos(tmp_path_factory):
    d = tmp_path_factory.mktemp("videos")
    _ffmpeg(*SOURCES, *ENCODE, "-metadata:s:a:0", "language=eng", str(d / "good.mkv"))
    _ffmpeg(*SOURCES, *ENCODE, "-metadata:s:a:0", "language=eng", "-movflags", "+faststart", str(d / "good.mp4"))
    _ffmpeg(*SOURCES, *ENCODE, "-metadata:s:a:0", "language=eng", str(d / "late_index.mp4"))
    with open(d / "no_index.mkv", "wb") as out:  # piped output: no seek index, no duration
        _ffmpeg(*SOURCES, *ENCODE, "-metadata:s:a:0", "language=eng", "-f", "matroska", "-", stdout=out)
    _truncate(d / "good.mkv", d / "truncated.mkv")
    _truncate(d / "good.mp4", d / "truncated.mp4")
    _truncate(d / "late_index.mp4", d / "unopenable.mp4")  # cut before its index
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc=duration=3:size=160x120:rate=10",
        "-f", "lavfi", "-i", "sine=duration=3", "-f", "lavfi", "-i", "sine=frequency=880:duration=3",
        "-map", "0", "-map", "1", "-map", "2", *ENCODE,
        "-disposition:a:0", "0", "-disposition:a:1", "0",
        # Older ffmpeg (4.4, Ubuntu 22.04) marks the first track default
        # unless told to pass the flags through as given.
        "-default_mode", "passthrough", str(d / "two_audio.mkv"),
    )
    return d


def _codes(result):
    return {f.code for f in result.findings}


def test_intact_files_check_ok(videos):
    for name in ("good.mkv", "good.mp4"):
        result = fc.quick_check(str(videos / name))
        assert result.status == "CHECKED OK", (name, result.summary())
        assert not result.can_repair


def test_structure_findings(videos):
    assert _codes(fc.quick_check(str(videos / "no_index.mkv"))) == {"no_index", "no_duration"}
    late = fc.quick_check(str(videos / "late_index.mp4"))
    assert _codes(late) == {"index_at_end"} and late.status == "REPAIRABLE" and late.can_repair
    assert _codes(fc.quick_check(str(videos / "two_audio.mkv"))) >= {"no_default_audio", "no_language"}


def test_truncated_files_are_damaged_but_repairable(videos):
    for name in ("truncated.mkv", "truncated.mp4"):
        result = fc.quick_check(str(videos / name))
        assert result.status == "DAMAGED" and result.can_repair, (name, result.summary())
        assert {"read_errors", "ends_early"} <= _codes(result)
        assert result.readable_seconds < result.declared_seconds - 3
        assert "Ends early: plays 0:0" in result.summary()


def test_unopenable_file_is_not_offered_for_repair(videos):
    result = fc.quick_check(str(videos / "unopenable.mp4"))
    assert result.status == "DAMAGED" and not result.openable and not result.can_repair
    assert "Can't be opened" in result.summary() and str(videos) not in result.summary()


def test_structure_readers(videos):
    assert fc.mkv_has_seek_index(str(videos / "good.mkv")) is True
    assert fc.mkv_has_seek_index(str(videos / "no_index.mkv")) is False
    assert fc.mkv_has_seek_index(str(videos / "good.mp4")) is None  # not Matroska
    assert fc.mp4_index_at_end(str(videos / "late_index.mp4")) is True
    assert fc.mp4_index_at_end(str(videos / "good.mp4")) is False


def _copy(videos, tmp_path, name):
    target = tmp_path / name
    shutil.copyfile(videos / name, target)
    return str(target)


@pytest.mark.parametrize("name", ["no_index.mkv", "late_index.mp4", "truncated.mkv", "truncated.mp4", "two_audio.mkv"])
def test_repair_fixes_and_sends_the_original_to_the_trash(videos, tmp_path, name):
    path = _copy(videos, tmp_path, name)
    before = fc.quick_check(path)
    trashed = []
    outcome = fc.repair(path, before, trash=trashed.append, restore=lambda _o, _r: "")
    assert trashed == [path]
    assert outcome.check.status in ("CHECKED OK", "NOTE"), outcome.check.summary()
    assert not outcome.check.can_repair
    assert os.listdir(tmp_path) == [name]  # no leftover .repairing copy
    if name.startswith("truncated"):
        # Everything readable is kept, minus the half-written last second.
        assert outcome.check.readable_seconds >= before.readable_seconds - fc._DAMAGED_TAIL_SECONDS - 0.5


def test_repair_keeps_the_apps_mp4_tags_and_cover(videos, tmp_path):
    from core.mp4_backend import read_mp4_cover, read_mp4_metadata, write_mp4_cover, write_mp4_metadata
    from core.video_metadata import VideoMetadata

    path = _copy(videos, tmp_path, "late_index.mp4")
    meta = VideoMetadata(
        title="The Gift", description="Visits.", genre_tags="Action, Drama", release_date="2018-06-30",
        language="eng", personal_rating=4, director="Tom King", cast="A, B", writer="W", studio="DC",
        collection="Batman", show_title="Show", season_number=2, episode_number=5, network="HBO",
    )
    write_mp4_metadata(path, meta)
    _ffmpeg("-f", "lavfi", "-i", "color=red:s=32x32", "-frames:v", "1", str(tmp_path / "cover.jpg"))
    cover = (tmp_path / "cover.jpg").read_bytes()
    (tmp_path / "cover.jpg").unlink()
    write_mp4_cover(path, cover)
    before_meta = read_mp4_metadata(path)
    # These custom fields are exactly what a plain ffmpeg remux drops.
    assert before_meta.director == "Tom King" and before_meta.collection == "Batman"
    outcome = fc.repair(path, fc.quick_check(path), trash=lambda _p: None)
    assert read_mp4_metadata(path) == before_meta
    assert read_mp4_cover(path) == cover
    assert "index_at_end" not in _codes(outcome.check)


def test_repair_falls_back_when_a_stream_cannot_be_copied(videos, tmp_path):
    """A stream a fresh container refuses (data, an unparsable cover)
    makes the full copy fail: repair retries with fewer streams."""
    path = _copy(videos, tmp_path, "late_index.mp4")
    attempts = []

    def picky_run(args, **kwargs):
        if args[0] == "ffmpeg" and "null" not in args:  # the remux, not the check's read pass
            maps = args[args.index("-i") + 2: args.index("-c")]
            attempts.append(maps)
            if maps[:2] == ["-map", "0"]:
                return subprocess.CompletedProcess(args, 1, "", "Could not write header (incorrect codec parameters ?)")
        return fc._run(args, **kwargs)

    outcome = fc.repair(path, fc.quick_check(path), run=picky_run, trash=lambda _p: None, restore=lambda _o, _r: "")
    assert len(attempts) == 3 and attempts[2][:2] == ["-map", "0:V"]
    assert outcome.check.status in ("CHECKED OK", "NOTE")


def test_a_failed_repair_leaves_the_original_alone(videos, tmp_path):
    path = _copy(videos, tmp_path, "late_index.mp4")
    original = open(path, "rb").read()
    before = fc.quick_check(path)

    def bad_restore(_original, _repaired):
        raise fc.RepairError("the repaired copy's metadata didn't match the original's")

    trashed = []
    with pytest.raises(fc.RepairError, match="didn't match"):
        fc.repair(path, before, trash=trashed.append, restore=bad_restore)
    assert not trashed and open(path, "rb").read() == original
    assert os.listdir(tmp_path) == ["late_index.mp4"]

    def no_trash(_path):
        raise fc.TrashError("couldn't move it to the Recycle Bin: no Recycle Bin on this drive")

    with pytest.raises(fc.RepairError, match="Recycle Bin"):
        fc.repair(path, before, trash=no_trash, restore=lambda _o, _r: "")
    assert open(path, "rb").read() == original and os.listdir(tmp_path) == ["late_index.mp4"]


def test_repair_refuses_what_it_cannot_fix(videos, tmp_path):
    with pytest.raises(fc.RepairError, match="nothing"):
        fc.repair(_copy(videos, tmp_path, "good.mkv"), fc.quick_check(str(videos / "good.mkv")))
    unopenable = fc.quick_check(str(videos / "unopenable.mp4"))
    with pytest.raises(fc.RepairError):
        fc.repair(_copy(videos, tmp_path, "unopenable.mp4"), unopenable)


def test_mkv_restore_without_mkvtoolnix_says_so(videos, tmp_path, monkeypatch):
    monkeypatch.setattr(fc, "is_tool_available", lambda _tool: False)
    path = _copy(videos, tmp_path, "no_index.mkv")
    outcome = fc.repair(path, fc.quick_check(path), trash=lambda _p: None)
    assert "MKVToolNix isn't installed" in outcome.note


# ---------------------------------------------------------------------------
# The dialog and flow
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication

    return QApplication.instance() or QApplication(sys.argv)


def test_check_and_repair_flow(videos, tmp_path, qapp, monkeypatch):
    from PyQt6.QtWidgets import QDialog, QMessageBox

    from core.video_file import VideoFile
    from gui import file_check_dialog as flow

    files = []
    for name in ("good.mkv", "late_index.mp4", "unopenable.mp4"):
        vf = VideoFile(path=tmp_path / name)
        shutil.copyfile(videos / name, vf.path)
        vf.load()
        files.append(vf)

    from PyQt6.QtWidgets import QStatusBar, QWidget

    class Window(QWidget):
        def __init__(self):
            super().__init__()
            self.status_bar = QStatusBar(self)
            self.refreshed = 0

        def _refresh_table_rows(self):
            self.refreshed += 1

    window = Window()
    shown = {}

    def fake_exec(dialog):
        shown["rows"] = dialog.table.rowCount()
        shown["repairable"] = [vf.path.name for vf in dialog.repairable]
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(flow.FileCheckResultsDialog, "exec", fake_exec)
    monkeypatch.setattr(flow.QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(flow.QMessageBox, "warning", lambda *a, **k: None)
    monkeypatch.setattr(flow.QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr("core.file_check.move_to_trash", lambda _p: None)
    # repair() took move_to_trash as a default argument at import time:
    monkeypatch.setattr(fc.repair, "__defaults__", (fc._run, lambda _p: None, fc.restore_app_metadata))

    flow.run_check_and_repair(window, files, lambda lines: "\n".join(lines))
    assert shown == {"rows": 2, "repairable": ["late_index.mp4"]}  # good.mkv has nothing to report
    assert [vf.check.status for vf in files] == ["CHECKED OK", "CHECKED OK", "DAMAGED"]
    assert "Repaired 1 of 1" in window.status_bar.currentMessage()


def test_unsaved_files_are_not_repaired(videos, tmp_path, qapp):
    from core.video_file import VideoFile
    from gui import file_check_dialog as flow

    vf = VideoFile(path=tmp_path / "late_index.mp4")
    shutil.copyfile(videos / "late_index.mp4", vf.path)
    vf.load()
    vf.check = fc.quick_check(str(vf.path))
    vf.dirty = True
    repaired, errors, _notes = flow.repair_files(None, [vf])
    assert not repaired and "unsaved changes" in errors[0]


def test_one_files_unexpected_error_does_not_abort_the_rest(tmp_path, qapp, monkeypatch):
    # M8: only RepairError used to be caught per file; an OSError, a
    # mutagen error or a timeout escaped and dropped every later file.
    from PyQt6.QtWidgets import QWidget

    from core.video_file import VideoFile
    from gui import file_check_dialog as flow

    files = []
    for name in ("a.mkv", "b.mkv"):
        vf = VideoFile(path=tmp_path / name)
        vf.check = fc.CheckResult(findings=[fc.Finding("index_at_end", fc.REPAIRABLE, "x")])
        files.append(vf)

    def fake_check(path):
        if path.endswith("a.mkv"):
            raise subprocess.TimeoutExpired("ffmpeg", 1)
        return fc.CheckResult()

    monkeypatch.setattr(flow, "quick_check", fake_check)
    errors = []
    checked = flow.check_files(QWidget(), files, errors)
    assert [vf.path.name for vf in checked] == ["b.mkv"] and len(errors) == 1 and errors[0].startswith("a.mkv:")

    def fake_repair(path, check):
        if path.endswith("a.mkv"):
            raise PermissionError("denied")
        raise fc.RepairError("nope")

    monkeypatch.setattr(flow, "repair", fake_repair)
    repaired, errors, _notes = flow.repair_files(QWidget(), files)
    assert repaired == [] and [e.split(":")[0] for e in errors] == ["a.mkv", "b.mkv"]


def test_a_tag_restore_failure_becomes_a_repair_error_and_leaves_the_original(videos, tmp_path):
    src = tmp_path / "late_index.mp4"
    shutil.copyfile(videos / "late_index.mp4", src)
    before = fc.quick_check(str(src))

    def broken_restore(_original, _repaired):
        raise OSError("disk hiccup")

    with pytest.raises(fc.RepairError, match="disk hiccup"):
        fc.repair(str(src), before, restore=broken_restore, trash=lambda _p: None)
    assert [p.name for p in tmp_path.iterdir()] == ["late_index.mp4"]
