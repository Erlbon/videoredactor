"""The Join Files window flow (offscreen, real small MP4 files): the dialog lists each movie with its parts and whether they
can be joined, a movie that cannot be joined cannot be ticked, and after a real join the list holds the joined file
instead of its parts."""

import dataclasses
import os
import shutil

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QMessageBox

from core import join_files as jf
from core import library_safety
from core.video_file import VideoFile
from gui import join_files_dialog as dialog_mod
from tests.test_file_check import qapp  # noqa: F401
from tests.test_track_language_gui import window  # noqa: F401
from tests.track_language_helpers import ffmpeg, make_mp4

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg/ffprobe not installed",
)


def _load(window, *paths):
    files = []
    for path in paths:
        vf = VideoFile(path=path)
        vf.load()
        files.append(vf)
    window.video_files = files
    window._refresh_table_rows()
    return files


def _no_recent_guard():
    library_safety.set_safety_settings(dataclasses.replace(library_safety.get_safety_settings(), recent_minutes=0))


def test_the_dialog_lists_movies_and_parts_and_blocks_a_movie_that_cannot_be_joined(window, tmp_path):
    good = [tmp_path / "Good cd1.mp4", tmp_path / "Good cd2.mp4"]
    for path in good:
        make_mp4(path, audio_tracks=1)
    make_mp4(tmp_path / "Bad cd1.mp4", audio_tracks=1)
    ffmpeg("-f", "lavfi", "-i", "testsrc2=size=320x240:rate=10:duration=2", "-f", "lavfi", "-i", "sine=duration=2",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast", "-c:a", "aac", "-shortest",
           str(tmp_path / "Bad cd2.mp4"))
    files = _load(window, *good, tmp_path / "Bad cd1.mp4", tmp_path / "Bad cd2.mp4")
    plans = dialog_mod.check_groups(window, files)
    dialog = dialog_mod.JoinFilesDialog(plans, window)
    assert dialog.tree.topLevelItemCount() == 2
    rows = {dialog.tree.topLevelItem(i).text(0).split(" (")[0]: dialog.tree.topLevelItem(i) for i in range(2)}
    assert rows["Good.mp4"].childCount() == 2 and rows["Good.mp4"].text(2) == "yes"
    assert rows["Good.mp4"].checkState(0) == Qt.CheckState.Checked
    assert rows["Bad.mp4"].text(2) == "no" and "re-encode" in rows["Bad.mp4"].text(3)
    assert not (rows["Bad.mp4"].flags() & Qt.ItemFlag.ItemIsUserCheckable)
    assert dialog.join_button.text() == "Join 1 movie" and dialog.join_button.isEnabled()
    rows["Good.mp4"].setCheckState(0, Qt.CheckState.Unchecked)
    assert not dialog.join_button.isEnabled()


def test_a_real_join_replaces_the_parts_in_the_list_with_the_joined_file(window, tmp_path, monkeypatch):
    _no_recent_guard()
    a, b = tmp_path / "Movie cd1.mp4", tmp_path / "Movie cd2.mp4"
    make_mp4(a, audio_tracks=1)
    make_mp4(b, audio_tracks=1)
    _load(window, a, b)
    window.table.selectAll()
    monkeypatch.setattr(dialog_mod.JoinFilesDialog, "exec", lambda self: (self._on_accept(), QDialog.DialogCode.Accepted)[1])
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    dialog_mod.run_join_files(window, window._selected_video_files(), lambda lines: "\n".join(lines))
    assert [vf.path.name for vf in window.video_files] == ["Movie.mp4"]
    assert (tmp_path / "Movie.mp4").is_file() and not a.exists() and not b.exists()
    assert "1 of 1 movie(s) joined" in window.status_bar.currentMessage()


def test_a_dry_run_changes_nothing_and_the_list_stays(window, tmp_path, monkeypatch):
    _no_recent_guard()
    a, b = tmp_path / "Movie cd1.mp4", tmp_path / "Movie cd2.mp4"
    make_mp4(a, audio_tracks=1)
    make_mp4(b, audio_tracks=1)
    _load(window, a, b)

    def accept_dry(self):
        self.dry_run_box.setChecked(True)
        self._on_accept()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(dialog_mod.JoinFilesDialog, "exec", accept_dry)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    dialog_mod.run_join_files(window, list(window.video_files), lambda lines: "\n".join(lines))
    assert len(window.video_files) == 2 and a.is_file() and b.is_file() and not (tmp_path / "Movie.mp4").exists()
    assert "Dry run: 1 of 1 movie(s) would be joined" in window.status_bar.currentMessage()


def test_one_file_is_not_enough(window, tmp_path, monkeypatch):
    a = tmp_path / "Movie cd1.mp4"
    make_mp4(a, audio_tracks=1)
    _load(window, a)
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda parent, title, text, *a, **k: shown.append((title, text)))
    dialog_mod.run_join_files(window, list(window.video_files), lambda lines: "\n".join(lines))
    assert shown and shown[0][0] == "Join Files"
