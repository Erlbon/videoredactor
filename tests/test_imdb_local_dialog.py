"""Metadata > Look Up > IMDb (Local Database): the search dialog (the TMDB dialog's flow, pointed at the
local database), the episode picker and the main-window import, headless."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtWidgets import QDialog, QMessageBox  # noqa: E402
from redactor_common.core.local_db import forget_cached  # noqa: E402

import gui.main_window as mw  # noqa: E402
from core import imdb_local, imdb_settings  # noqa: E402
from core.imdb_import import ImdbDatabaseError  # noqa: E402
from core.video_metadata import ContentType  # noqa: E402
from gui.imdb_episode_picker_dialog import ImdbEpisodePickerDialog  # noqa: E402
from gui.tmdb_search_dialog import SearchSource, TMDBSearchDialog  # noqa: E402
from tests.imdb_fixture import AKAS, BASICS, EPISODES, RATINGS, build  # noqa: E402
from tests.test_batch_operations_and_undo import _app, window  # noqa: E402,F401
from tests.test_imdb_local import OFFICE, OFFICE_EPISODES, OFFICE_RATINGS  # noqa: E402


@pytest.fixture
def imdb(tmp_path):
    path, _s, _f = build(tmp_path, basics=BASICS + OFFICE, ratings=RATINGS + OFFICE_RATINGS,
                         episodes=EPISODES + OFFICE_EPISODES, akas=AKAS)
    yield path
    forget_cached(path)


def source_for(path):
    db = imdb_local.open_database(path)
    return SearchSource(
        name="IMDb (Local Database)",
        movies=lambda q, year=None: imdb_local.search_movies(db, q, year),
        tv=lambda q, year=None: imdb_local.search_series(db, q, year),
        errors=(ImdbDatabaseError,), note="IMDb's datasets have no plot, poster or cast.", switchable=True,
    )


def test_the_search_dialog_lists_local_results_and_says_what_is_missing(imdb):
    dialog = TMDBSearchDialog("movie", "Zarnak", "1984", source=source_for(imdb))
    try:
        assert "IMDb (Local Database)" in dialog.windowTitle() and "Movie" in dialog.windowTitle()
        labels = [dialog.results_list.item(i).text() for i in range(dialog.results_list.count())]
        assert labels[0] == "Zarnak (1984)" and "Zarnak (2021)" in labels
        assert dialog.findChildren(type(dialog.overview_label))  # the detail box exists
        notes = [w.text() for w in dialog.findChildren(__import__("PyQt6.QtWidgets", fromlist=["QLabel"]).QLabel)]
        assert any("no plot, poster or cast" in n for n in notes)
        dialog.results_list.setCurrentRow(0)
        assert "tt90000001" in dialog.overview_label.toPlainText() and dialog.select_button.isEnabled()
        dialog._on_accept()
        assert dialog.selected_candidate.imdb_id == "tt90000001"
    finally:
        dialog.close()


def test_the_dialog_can_switch_between_film_and_tv_show(imdb):
    dialog = TMDBSearchDialog("movie", "Quillfeather", "", source=source_for(imdb))
    try:
        assert dialog.results_list.item(0).text() == "No results found"
        dialog.mode_combo.setCurrentIndex(dialog.mode_combo.findData("tv"))
        assert dialog.mode == "tv" and "TV Show" in dialog.windowTitle()
        assert dialog.results_list.item(0).text() == "Quillfeather (2008)"
    finally:
        dialog.close()


def test_an_alias_is_shown_in_the_result_label(imdb):
    dialog = TMDBSearchDialog("movie", "Il codice della brace", "", source=source_for(imdb))
    try:
        assert dialog.results_list.item(0).text() == "The Ember Cipher (1986) - also known as 'Il codice della brace'"
    finally:
        dialog.close()


def test_a_failing_search_is_reported_not_raised(imdb, monkeypatch):
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[1:3]))

    def broken(q, year=None):
        raise ImdbDatabaseError("the file is damaged")

    source = SearchSource("IMDb (Local Database)", broken, broken, (ImdbDatabaseError,))
    dialog = TMDBSearchDialog("movie", "Zarnak", source=source)
    try:
        assert warned == [("IMDb (Local Database) Search Failed", "the file is damaged")]
    finally:
        dialog.close()


def test_the_tmdb_dialog_is_unchanged_without_a_source(monkeypatch):
    from gui import tmdb_search_dialog as tmdb

    seen = []
    monkeypatch.setattr(tmdb, "search_movies", lambda q, year=None: seen.append((q, year)) or [])
    monkeypatch.setattr(tmdb, "search_tv", lambda q: seen.append(q) or [])
    dialog = TMDBSearchDialog("movie", "Alien", "1979")
    assert dialog.windowTitle() == "Search TMDB (Movie)" and dialog.mode_combo is None
    tv = TMDBSearchDialog("tv", "Alien")
    assert tv.year_edit is None and tv.windowTitle() == "Search TMDB (TV Show)"
    assert seen == [("Alien", "1979"), "Alien"]


def test_the_episode_picker_preselects_from_the_filename(imdb):
    dialog = ImdbEpisodePickerDialog(imdb, 90000021, "Harbor Lights", initial_season=2, initial_episode=5)
    try:
        assert dialog.season_combo.currentData() == 2
        assert dialog.episode_list.currentItem().text() == "E5: Moulting"
        assert "tt90000022" in dialog.overview_box.toPlainText()
        dialog._on_accept()
        assert (dialog.selected_season, dialog.selected_episode.name) == (2, "Moulting")
    finally:
        dialog.close()


def test_the_episode_picker_with_no_episodes_says_so(imdb, monkeypatch):
    told = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: told.append(a[2]))
    dialog = ImdbEpisodePickerDialog(imdb, 90000001, "Zarnak")
    assert told and "no episodes" in told[0] and dialog.result() == QDialog.DialogCode.Rejected


# --- the main window ----------------------------------------------------------------------------


def configure_db(path):
    imdb_settings.save_database(path)


def test_without_a_database_it_offers_the_settings_dialog(window, monkeypatch):
    asked, opened = [], []
    window.table.selectAll()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(mw.MainWindow, "_on_open_imdb_settings", lambda self: opened.append(True))
    monkeypatch.setattr(mw, "TMDBSearchDialog", lambda *a, **k: pytest.fail("no database: no search"))
    window._on_import_imdb_local()
    assert asked and "Tools > IMDb Database" in asked[0] and opened == [True]


def test_nothing_selected_says_so(window, monkeypatch):
    told = []
    window.table.clearSelection()
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: told.append(a[1]))
    window._on_import_imdb_local()
    assert told == ["No Files Selected"]


def test_a_broken_database_is_reported_before_any_dialog(window, monkeypatch, tmp_path):
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"not sqlite")
    configure_db(str(bad))
    warned = []
    window.table.selectAll()
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    monkeypatch.setattr(mw, "TMDBSearchDialog", lambda *a, **k: pytest.fail("no search on a broken database"))
    window._on_import_imdb_local()
    assert warned and "Tools > IMDb Database" in warned[0]


class FakeSearch:
    """Stands in for TMDBSearchDialog: accepts the first candidate of the real local search."""

    seen = []

    def __init__(self, mode, initial_query="", initial_year="", parent=None, source=None):
        self.mode = mode
        FakeSearch.seen.append((mode, initial_query, initial_year))
        results = (source.tv if mode == "tv" else source.movies)(initial_query, initial_year or None)
        self.selected_candidate = results[0] if results else None

    def exec(self):
        return bool(self.selected_candidate)


def test_the_import_confirms_a_show_once_and_fills_episodes(window, monkeypatch, imdb):
    configure_db(imdb)
    FakeSearch.seen = []
    window.video_files[0].metadata.show_title = ""
    window.video_files[0].metadata.title = ""
    window.video_files[1].metadata.show_title = ""
    window.video_files[1].metadata.title = ""
    window.video_files[0].path = window.video_files[0].path.with_name("Harbor Lights (2005) S02E05.mkv")
    window.video_files[1].path = window.video_files[1].path.with_name("Harbor Lights (2005) S01E01.mkv")
    window.table.selectAll()
    picks = []

    class Picker:
        def __init__(self, db_path, series, name, initial_season=None, initial_episode=None, parent=None):
            picks.append((series, initial_season, initial_episode))
            self.selected_episode = imdb_local.find_episode(imdb_local.open_database(db_path), series,
                                                            initial_season, initial_episode)

        def exec(self):
            return True

    monkeypatch.setattr(mw, "TMDBSearchDialog", FakeSearch)
    import gui.imdb_episode_picker_dialog as picker_module
    monkeypatch.setattr(picker_module, "ImdbEpisodePickerDialog", Picker)
    window._on_import_imdb_local()
    assert len(FakeSearch.seen) == 1  # the same show: asked once
    first, second = window.video_files
    assert (first.metadata.show_title, first.metadata.title, first.metadata.season_number, first.metadata.episode_number) == (
        "Harbor Lights", "Moulting", 2, 5)
    assert (second.metadata.title, second.metadata.episode_number) == ("Hatching", 1)
    assert first.metadata.content_type is ContentType.TV and first.dirty and second.dirty
    assert first.metadata.release_date == "2005" and first.metadata.genre_tags == "Comedy, Science Fiction"
    assert "Imported IMDb metadata for 2 file(s)" in window.status_bar.currentMessage()
    window.undo_last_action()
    assert window.video_files[0].metadata.title == "" and not window.video_files[0].dirty


def test_a_film_import_fills_only_imdb_fields_and_keeps_a_fuller_date(window, monkeypatch, imdb, tmp_path):
    configure_db(imdb)
    vf = window.video_files[0]
    vf.path = vf.path.with_name("Zarnak.1984.mkv")
    vf.metadata.release_date = "1984-12-14"
    vf.metadata.description = "mine"
    vf.metadata.title = ""
    window.table.clearSelection()
    window.table.selectRow(0)
    monkeypatch.setattr(mw, "TMDBSearchDialog", FakeSearch)
    FakeSearch.seen = []
    window._on_import_imdb_local()
    md = vf.metadata
    assert FakeSearch.seen == [("movie", "Zarnak", "1984")]
    assert (md.title, md.release_date, md.description, md.content_type) == ("Zarnak", "1984-12-14", "mine", ContentType.MOVIE)
    assert md.genre_tags == "Adventure, Drama, Science Fiction"
    assert md.director == "" and md.cast == ""  # nothing IMDb doesn't have


def test_cancelling_the_search_changes_nothing(window, monkeypatch, imdb):
    configure_db(imdb)
    window.table.selectRow(0)

    class Cancelled(FakeSearch):
        def exec(self):
            return False

    monkeypatch.setattr(mw, "TMDBSearchDialog", Cancelled)
    before = window.video_files[0].metadata.title
    window._on_import_imdb_local()
    assert window.video_files[0].metadata.title == before and not window.video_files[0].dirty
    assert "1 skipped (no match confirmed)" in window.status_bar.currentMessage()


def test_the_redact_env_carries_the_database_path(window, monkeypatch, tmp_path):
    configure_db(str(tmp_path / "x.db"))
    seen = {}

    def fake_run(*args, **kwargs):
        seen["env"] = kwargs["make_context"].__closure__[0].cell_contents
        return None

    monkeypatch.setattr(mw, "run_redact_dialog", fake_run)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    window.table.selectAll()
    window._on_redact()
    assert seen["env"].imdb_local == str(tmp_path / "x.db")
