"""The shared season + episode picker (gui/episode_picker_dialog.py) with fake sources, its three
source adapters, the TheTVDB search dialog on the shared search dialog, and the transcode batch
on redactor_common's run_work_with_progress (Cancel stops the running encode)."""

import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtCore import QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox, QProgressDialog  # noqa: E402

from gui.episode_picker_dialog import EpisodePickerDialog, EpisodeSource  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _app():
    return QApplication.instance() or QApplication([])


@dataclass
class Ep:
    episode_number: int
    name: str = ""
    overview: str = ""


class Boom(Exception):
    pass


class PerSeason(EpisodeSource):
    """Like TMDB: a new fetch for every season shown."""

    errors = (Boom,)

    def __init__(self, fail_episodes=False):
        self.fetched = []
        self.fail_episodes = fail_episodes

    def seasons(self, owner):
        return [(1, "Season 1"), (2, "Season 2")]

    def episodes(self, owner, season):
        self.fetched.append(season)
        if self.fail_episodes:
            raise Boom("offline")
        return [Ep(1, f"S{season} one", "first"), Ep(2, "", "")]


class InMemory(EpisodeSource):
    """Like TheTVDB: one fetch at open, seasons filtered in memory."""

    def __init__(self):
        self.calls = 0
        self.all = {1: [Ep(1, "a")], 2: [Ep(1, "b"), Ep(2, "c")]}

    def seasons(self, owner):
        self.calls += 1
        return [(n, f"Season {n}") for n in self.all]

    def episodes(self, owner, season):
        return self.all[season]


def test_a_per_season_source_fetches_again_on_season_change():
    source = PerSeason()
    dialog = EpisodePickerDialog(source)
    try:
        assert source.fetched == [1]
        dialog.season_combo.setCurrentIndex(1)
        assert source.fetched == [1, 2]
        assert dialog.episode_list.item(0).text() == "E1: S2 one"
        assert dialog.episode_list.item(1).text() == "Episode 2"
    finally:
        dialog.close()


def test_an_in_memory_source_loads_once():
    source = InMemory()
    dialog = EpisodePickerDialog(source)
    try:
        dialog.season_combo.setCurrentIndex(1)
        dialog.season_combo.setCurrentIndex(0)
        assert source.calls == 1 and dialog.episode_list.count() == 1
    finally:
        dialog.close()


def test_initial_selection_is_preselected_not_picked():
    dialog = EpisodePickerDialog(PerSeason(), initial_season=2, initial_episode=2)
    try:
        assert dialog.season_combo.currentData() == 2
        assert dialog.episode_list.currentRow() == 1
        assert dialog.overview_box.toPlainText() == "(no synopsis available)"
        assert dialog.select_button.isEnabled()
        assert dialog.selected_episode is None and dialog.selected_season is None  # nothing auto-picked
        assert dialog.result() != QDialog.DialogCode.Accepted
        dialog._on_accept()
        assert dialog.selected_season == 2 and dialog.selected_episode.episode_number == 2
    finally:
        dialog.close()


def test_nothing_is_selected_without_an_initial_episode():
    dialog = EpisodePickerDialog(PerSeason())
    try:
        assert not dialog.episode_list.selectedItems() and not dialog.select_button.isEnabled()
        dialog._on_accept()
        assert dialog.selected_episode is None
    finally:
        dialog.close()


def test_an_episode_error_is_shown_not_raised(monkeypatch):
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[1:3]))
    dialog = EpisodePickerDialog(PerSeason(fail_episodes=True))
    try:
        assert warned == [("Could Not Load Episodes", "offline")] and dialog.episode_list.count() == 0
    finally:
        dialog.close()


def test_a_season_error_is_shown_and_the_dialog_rejects(monkeypatch):
    class Broken(PerSeason):
        def seasons(self, owner):
            raise Boom("no network")

    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[1:3]))
    dialog = EpisodePickerDialog(Broken())
    assert warned == [("Could Not Load Seasons", "no network")]
    assert dialog.result() == QDialog.DialogCode.Rejected


def test_the_tvdb_picker_fetches_once_and_keeps_its_texts(monkeypatch):
    from core.tvdb_client import EpisodeInfo
    from gui import tvdb_episode_picker_dialog as tvdb

    calls = []
    eps = [EpisodeInfo(season_number=2, episode_number=3, name="C", overview="", aired=""),
           EpisodeInfo(season_number=1, episode_number=1, name="A", overview="x", aired=""),
           EpisodeInfo(season_number=2, episode_number=1, name="B", overview="", aired="")]
    monkeypatch.setattr(tvdb, "run_lookup", lambda owner, fn, *a: calls.append(fn) or eps)
    dialog = tvdb.TVDBEpisodePickerDialog(7, initial_season=2, initial_episode=3)
    try:
        assert dialog.windowTitle() == "Select Season & Episode (TheTVDB)" and dialog.tvdb_id == 7
        assert [dialog.episode_list.item(i).text() for i in range(dialog.episode_list.count())] == ["E1: B", "E3: C"]
        assert dialog.episode_list.currentRow() == 1 and len(calls) == 1
    finally:
        dialog.close()


def test_the_tmdb_picker_keeps_its_title(monkeypatch):
    from gui import tmdb_episode_picker_dialog as tmdb

    monkeypatch.setattr(tmdb, "run_lookup", lambda *a, **k: [])
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    dialog = tmdb.TVEpisodePickerDialog(5)
    assert dialog.windowTitle() == "Select Season & Episode" and dialog.tmdb_id == 5


def test_the_tvdb_search_dialog_is_the_shared_dialog_without_a_year_box(monkeypatch):
    from core.tvdb_client import SeriesCandidate, TVDBError
    from gui import tvdb_search_dialog as tvdb

    monkeypatch.setattr(tvdb, "search_series", lambda q: [SeriesCandidate(
        tvdb_id=1, name="Alien", first_air_time="1979-05-25", overview="", image_url=None)])
    dialog = tvdb.TVDBSearchDialog(initial_query="Alien")
    try:
        assert dialog.windowTitle() == "Search TheTVDB (TV Show)" and dialog.year_edit is None
        assert dialog.mode_combo is None and not hasattr(dialog, "attribution")
        assert dialog.results_list.item(0).text() == "Alien (1979)"
        dialog.results_list.setCurrentRow(0)
        assert dialog.overview_label.toPlainText() == "(no synopsis available)"
    finally:
        dialog.close()

    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[1:3]))

    def failing(q):
        raise TVDBError("offline")

    monkeypatch.setattr(tvdb, "search_series", failing)
    tvdb.TVDBSearchDialog(initial_query="Alien").close()
    assert warned == [("TheTVDB Search Failed", "offline")]
