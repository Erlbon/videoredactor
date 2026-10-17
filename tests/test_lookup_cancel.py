"""Cancel in a batch lookup cancels the WHOLE lookup (owner, 2026-10-17); the new Skip button is what Cancel used to be.

Real MainWindow, the real episode picker and the real TMDB search dialog, offscreen; the scripted part only presses the
dialogs' real buttons / sends the real Esc key / closes the window like the X does. In a 4-file batch:
- Cancel (button, Esc, X) on file 2: files 3 and 4 never get a dialog, file 1's confirmed result is kept and undoable,
  files 2-4 are untouched;
- Skip on file 2: file 3 is asked."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog  # noqa: E402

import gui.main_window as mw  # noqa: E402
from core.video_file import VideoFile  # noqa: E402
from core.video_metadata import VideoMetadata  # noqa: E402
from gui.episode_picker_dialog import EpisodePickerDialog, EpisodeSource  # noqa: E402
from gui.lookup_file_header import file_context  # noqa: E402
from gui.tmdb_search_dialog import TMDBSearchDialog  # noqa: E402
from tests.test_per_file_confirmation import NAMES, Script, boxes, close, make_window, setup_tmdb, state  # noqa: E402,F401

_app = QApplication.instance() or QApplication([])


class Source(EpisodeSource):
    def seasons(self, owner):
        return [(1, "Season 1")]

    def episodes(self, owner, season):
        return [type("E", (), {"episode_number": n, "name": f"Ep {n}", "overview": ""})() for n in range(1, 5)]


def run_scripted(monkeypatch, tmp_path, actions):
    """Runs the real TMDB TV import over four files; `actions` maps a file name to what the owner does in its picker:
    episode / skip / cancel / esc / x. Returns (window, names of the files whose picker was opened, before-states)."""
    asked: list[str] = []

    class Real(EpisodePickerDialog):
        def __init__(self, tmdb_id, initial_season=None, initial_episode=None, parent=None, context=None):
            super().__init__(Source(), parent, initial_season, initial_episode, context=context)
            self.file_name = context.file.path.name
            self.episode_number = int(self.file_name.split("E")[1][:2])

        def exec(self):
            asked.append(self.file_name)
            action = actions[self.file_name]
            if action == "episode":
                self.episode_list.setCurrentRow(self.episode_number - 1)
                self.select_button.click()
            elif action == "skip":
                self.skip_button.click()
            elif action == "cancel":
                self.cancel_button.click()
            elif action == "esc":
                QTest.keyClick(self, Qt.Key.Key_Escape)
            elif action == "x":
                self.show()
                self.close()
            return self.result()

    w = make_window(monkeypatch, tmp_path, NAMES)
    before = [state(vf) for vf in w.video_files]
    setup_tmdb(monkeypatch, Script({}))
    monkeypatch.setattr(mw, "TVEpisodePickerDialog", Real)
    w._import_tmdb_tv(w.video_files, 0)
    return w, asked, before


@pytest.mark.parametrize("how", ["cancel", "esc", "x"])
def test_cancel_on_file_two_cancels_the_whole_lookup(monkeypatch, tmp_path, how):
    actions = {NAMES[0]: "episode", NAMES[1]: how, NAMES[2]: "episode", NAMES[3]: "episode"}
    w, asked, before = run_scripted(monkeypatch, tmp_path, actions)
    assert asked == NAMES[:2]  # files 3 and 4 never got a dialog
    assert w.video_files[0].dirty and w.video_files[0].metadata.title == "Episode 1"  # file 1's result is kept
    for vf, old in zip(w.video_files[1:], before[1:]):
        assert state(vf) == old and not vf.dirty  # file 2 (cancelled here) and 3, 4 are untouched
    assert "stopped, 3 file(s) not asked" in w.status_bar.currentMessage()
    w.undo_last_action()
    assert [state(vf) for vf in w.video_files] == before  # file 1's staged result is undoable
    close(w)


def test_skip_on_file_two_goes_on_with_file_three(monkeypatch, tmp_path):
    actions = {NAMES[0]: "episode", NAMES[1]: "skip", NAMES[2]: "episode", NAMES[3]: "episode"}
    w, asked, before = run_scripted(monkeypatch, tmp_path, actions)
    assert asked == NAMES  # everyone was asked
    assert state(w.video_files[1]) == before[1] and not w.video_files[1].dirty
    assert all(w.video_files[i].dirty for i in (0, 2, 3))
    assert "1 skipped" in w.status_bar.currentMessage()
    close(w)


def test_the_picker_has_four_buttons_in_a_batch_and_a_plain_cancel_for_one_file(tmp_path):
    path = tmp_path / "Show.S01E01.mp4"
    path.write_bytes(b"x")
    vf = VideoFile(path=path, metadata=VideoMetadata())
    batch = EpisodePickerDialog(Source(), context=file_context(vf, 1, 3))
    assert [b.text() for b in (batch.select_button, batch.show_only_button, batch.skip_button, batch.cancel_button)] == [
        "Use This Episode", "Show Information Only", "Skip This File", "Cancel Lookup"]
    single = EpisodePickerDialog(Source(), context=file_context(vf))
    assert not hasattr(single, "skip_button") and single.cancel_button.text() == "Cancel"
    single.cancel_button.click()
    assert single.stop_requested is False and single.result() == QDialog.DialogCode.Rejected


def _search(tmp_path, index=2, total=5):
    path = tmp_path / "Inception.2010.mp4"
    path.write_bytes(b"x")
    vf = VideoFile(path=path, metadata=VideoMetadata())
    return TMDBSearchDialog("movie", initial_query="", context=file_context(vf, index, total) if total else None)


def test_the_search_dialog_bottom_row_is_skip_use_selected_cancel_in_a_batch(tmp_path):
    d = _search(tmp_path)
    d.resize(700, 500)
    d.show()
    QApplication.processEvents()
    buttons = [d.skip_button, d.select_button, d.cancel_button]
    assert [b.text() for b in buttons] == ["Skip This File", "Use Selected", "Cancel"]
    xs = [b.mapTo(d, b.rect().topLeft()).x() for b in buttons]
    assert xs == sorted(xs) and len(set(xs)) == 3  # left to right in that order
    d.skip_button.click()
    assert d.stop_requested is False and d.result() == QDialog.DialogCode.Rejected


@pytest.mark.parametrize("how", ["cancel", "esc", "x"])
def test_the_search_dialog_cancel_esc_and_x_cancel_the_lookup_in_a_batch(tmp_path, how):
    d = _search(tmp_path)
    d.show()
    if how == "cancel":
        d.cancel_button.click()
    elif how == "esc":
        QTest.keyClick(d, Qt.Key.Key_Escape)
    else:
        d.close()
    assert d.stop_requested is True and d.result() == QDialog.DialogCode.Rejected


def test_a_single_file_search_has_no_skip_button_and_cancel_just_closes(tmp_path):
    d = _search(tmp_path, total=None)
    assert not hasattr(d, "skip_button")
    d.cancel_button.click()
    assert d.stop_requested is False and d.result() == QDialog.DialogCode.Rejected
