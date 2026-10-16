"""Tools > Create Notes... through the REAL main window: real folders of video files, the real engine and the real write
function, recorded-shape TMDB / Wikipedia replies as the network (nothing reaches the internet), the dialogs driven by their
widgets with QTest clicks. The script is a generator stepped by a timer, so the modal dialogs can be driven."""

import os
import time
import traceback
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtCore import Qt, QTimer  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox  # noqa: E402

from core import config, notes_settings  # noqa: E402
from gui.notes_dialog import (  # noqa: E402
    folder_caption, folder_info, scope_candidates, scope_hint, title_folders,
)
from redactor_common.gui.notes_review_dialog import NotesReviewDialog  # noqa: E402
from redactor_common.gui.notes_scope_dialog import NotesScopeDialog  # noqa: E402
from tests.notes_helpers import BREAKING_BAD, fake_fetchers, hit, make_movie, make_series, touch  # noqa: E402
from tests.test_batch_operations_and_undo import _app, window  # noqa: E402,F401


class Script:
    def __init__(self, generator):
        self.gen, self.error, self.done = generator, "", False
        self.timer = QTimer()
        self.timer.timeout.connect(self.tick)
        self.busy = False
        self.timer.start(20)

    def tick(self):
        if self.busy or self.done:
            return
        self.busy = True
        try:
            next(self.gen)
        except StopIteration:
            self.done = True
        except BaseException:  # noqa: BLE001
            self.error, self.done = traceback.format_exc(), True
        finally:
            self.busy = False
        if self.done:
            self.timer.stop()
            for w in QApplication.topLevelWidgets():
                if isinstance(w, (NotesReviewDialog, NotesScopeDialog)) and w.isVisible():
                    w.done(0)


def review():
    found = [w for w in QApplication.topLevelWidgets() if isinstance(w, NotesReviewDialog) and w.isVisible()]
    return found[0] if found else None


def scope():
    found = [w for w in QApplication.topLevelWidgets() if isinstance(w, NotesScopeDialog) and w.isVisible()]
    return found[0] if found else None


def wait(cond, what, timeout=30):
    end = time.perf_counter() + timeout
    while not cond():
        assert time.perf_counter() < end, what
        yield


def click(widget):
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton)


@pytest.fixture
def boxes(monkeypatch):
    seen = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda p, t, x, *a, **k: seen.append((t, x)) or QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda p, t, x, *a, **k: seen.append((t, x)) or QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda p, t, x, *a, **k: seen.append((t, x)) or QMessageBox.StandardButton.No))
    return seen


def run(window, monkeypatch, folder, fetchers, scenario, must_finish=True):
    """Tools > Create Notes: the scope question first (the folder is picked with Another folder...), then the scenario."""
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(folder)))
    window.table.clearSelection()
    window.notes_fetchers = lambda: fetchers

    def with_scope_step():
        yield from wait(lambda: scope() is not None, "the scope dialog")
        dialog = scope()
        dialog.other_radio.click()
        click(dialog.continue_button)
        yield from scenario()

    script = Script(with_scope_step())
    window.actions_by_key["create_notes"].trigger()
    script.timer.stop()
    assert script.error == "", script.error
    assert script.done or not must_finish, "the scenario did not finish"


def test_a_single_series_opens_one_at_a_time_and_write_note_writes_series_md(window, monkeypatch, tmp_path, boxes):
    library = tmp_path / "Library"  # (the window fixture keeps its own video files in tmp_path itself)
    show = make_series(library)

    def scenario():
        yield from wait(lambda: review() is not None, "review page")
        dlg = review()
        assert dlg.view == "one" and dlg.header_label.text() == "Create notes  1 of 1"
        assert dlg.one_panel.path_label.text() == str(show) and dlg.one_panel.badge.text() == "Confident"
        assert dlg.one_panel.kind_label.text().startswith("Note: series.md")
        assert "It ran for five seasons." in dlg.one_panel.preview.toPlainText()
        assert dlg.one_panel.raw.toPlainText().startswith("---\ntitle: Breaking Bad")
        assert "Looked up: series and film notes." in dlg.banner_label.text() and "Looked up 1 folder" in dlg.banner_label.text()
        expected = dlg.session.items[0].proposal.note_text
        click(dlg.write_button)
        yield from wait(lambda: review() is None, "closed after the last note", 10)
        assert (show / "series.md").read_text(encoding="utf-8") == expected.replace("\r\n", "\n").rstrip("\n") + "\n"
        assert "status: draft" in expected.split("---")[1]

    run(window, monkeypatch, library, fake_fetchers(), scenario)
    assert any("Written: 1" in text for _title, text in boxes)
    assert notes_settings.last_folder() == str(library) and notes_settings.kinds() == ("series", "movie")


def multi_fetchers():
    other = hit("tv", 99, "Dark", "2017-12-01", "A German series")
    dark_two = hit("tv", 100, "Dark", "2019-01-01", "Another Dark")
    details = lambda ident, year: dict(BREAKING_BAD, id=ident, name="Dark", original_name="Dark", first_air_date=f"{year}-01-01",
                                       external_ids={"imdb_id": ""})
    return fake_fetchers(tmdb_extra=[(r"/search/tv\?.*query=Dark", {"results": [other, dark_two]}),
                                     (r"/tv/99\?", details(99, 2017)), (r"/tv/100\?", details(100, 2019))])


def test_several_folders_open_the_list_with_the_tick_rules_a_pick_and_a_write(window, monkeypatch, tmp_path, boxes):
    show = make_series(tmp_path / "Library")
    movie = make_movie(tmp_path / "Library")
    for episode in (1, 2):
        touch(tmp_path / "Library" / "Dark" / f"Dark.S01E0{episode}.mkv")  # two TMDB titles are called Dark

    def scenario():
        yield from wait(lambda: review() is not None, "review page")
        dlg = review()
        s = dlg.session
        assert dlg.view == "list"
        rows = {dlg.tree.topLevelItem(i).text(0).split("\\")[-1]: dlg.tree.topLevelItem(i) for i in range(dlg.tree.topLevelItemCount())}
        assert set(rows) == {show.name, movie.name, "Dark"}
        assert rows[show.name].checkState(0) == Qt.CheckState.Checked and rows[movie.name].checkState(0) == Qt.CheckState.Checked
        dark = rows["Dark"]
        assert not (dark.flags() & Qt.ItemFlag.ItemIsUserCheckable) and dark.checkState(0) != Qt.CheckState.Checked
        dark.setCheckState(0, Qt.CheckState.Checked)
        assert not s.items[dark.data(0, Qt.ItemDataRole.UserRole)].ticked
        # the uncertain one: pick the candidate, it becomes confident and ticked
        dlg.tree.setCurrentItem(dark)
        i = dark.data(0, Qt.ItemDataRole.UserRole)
        assert s.items[i].proposal.confidence.value == "uncertain" and dlg.list_panel.candidate_table.rowCount() == 2
        click(dlg.list_panel.candidate_button)
        yield from wait(lambda: s.items[i].proposal.confidence.value == "confident", "the rebuilt proposal")
        assert s.items[i].ticked
        # an existing note that appears after the scan blocks its write and is never touched
        blocker = show / "Series.MD"
        blocker.write_text("mine\n", encoding="utf-8")
        n = len(s.ticked_pending())
        click(dlg.write_ticked_button)
        assert blocker.read_text(encoding="utf-8") == "mine\n"  # untouched (on a case-insensitive disk it is series.md too)
        assert (movie / "about.md").is_file()
        assert f"Written: {n - 1}" in dlg.last_summary and "Blocked by an existing note: 1" in dlg.last_summary
        dlg.done(0)

    run(window, monkeypatch, tmp_path / "Library", multi_fetchers(), scenario)


def test_without_a_tmdb_key_it_says_so_and_looks_nothing_up(window, monkeypatch, tmp_path, boxes):
    make_series(tmp_path / "Library")
    from core.notes_flow import Fetchers

    fetchers = Fetchers(tmdb=lambda r: pytest.fail("no request without a key"), key=None)
    monkeypatch.setattr("gui.notes_dialog.get_api_key", lambda: None)
    monkeypatch.setattr("core.notes_tmdb.get_api_key", lambda: None)

    def scenario():
        yield from wait(lambda: any("API key" in text for _t, text in boxes), "the key message")

    run(window, monkeypatch, tmp_path / "Library", fetchers, scenario, must_finish=False)
    assert review() is None and any("API key" in text for _t, text in boxes)


def test_nothing_to_write_a_note_for_says_why(window, monkeypatch, tmp_path, boxes):
    show = make_series(tmp_path / "Library")
    (show / "series.md").write_text("mine\n", encoding="utf-8")

    def scenario():
        yield from wait(lambda: any("No series or film folders without a note" in text for _t, text in boxes), "the message")

    run(window, monkeypatch, tmp_path / "Library", fake_fetchers(), scenario, must_finish=False)
    assert any("No series or film folders without a note" in text and "1 folder(s) already have a note" in text for _t, text in boxes)


def test_the_scope_question_shows_what_each_folder_would_get_and_the_kinds_are_remembered(window, monkeypatch, tmp_path, boxes):
    make_series(tmp_path / "Library")
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(tmp_path / "Library")))
    window.notes_fetchers = lambda: fake_fetchers()

    def steps():
        yield from wait(lambda: scope() is not None, "the scope dialog")
        dialog = scope()
        dialog.other_radio.click()
        yield from wait(lambda: "library folder" in dialog.other_label.text(), "the caption of the picked folder")
        assert "1 series and 0 film folders below" in dialog.other_label.text()
        dialog.kind_boxes["movie"].setChecked(False)
        click(dialog.continue_button)
        yield from wait(lambda: review() is not None, "review page")
        assert "series notes only" in review().banner_label.text()
        review().done(0)

    script = Script(steps())
    window.actions_by_key["create_notes"].trigger()
    script.timer.stop()
    assert script.error == "", script.error
    assert notes_settings.kinds() == ("series",)


# ---- what a folder is, in words ---------------------------------------------------------------------------------------

def test_a_file_in_a_season_folder_belongs_to_its_series_and_each_folder_is_listed_once(tmp_path):
    show = make_series(tmp_path)
    movie = make_movie(tmp_path)
    files = [show / "Season 1" / "Breaking.Bad.S01E01.720p.mkv", show / "Season 2" / "Breaking.Bad.S02E01.720p.mkv",
             movie / "The.Matrix.1999.1080p.mkv"]
    assert title_folders(files) == [show, movie]


def test_the_scope_candidates_are_the_opened_folder_the_selection_one_level_up_and_the_last_folder(tmp_path):
    library = tmp_path / "Library"
    show = make_series(library)
    last = tmp_path / "Elsewhere"
    last.mkdir()
    loaded = [str(show / "Season 1" / "Breaking.Bad.S01E01.720p.mkv")]
    candidates = scope_candidates(loaded, loaded, str(show), str(last))
    assert [(Path(p).name, caption, default) for p, caption, default in candidates] == [
        (show.name, "the folder you opened: the loaded file is in it", False),
        (show.name, "the series or film of the selected files", True),
        ("Library", "one level up: also the other folders next to it", False),
        ("Elsewhere", "the folder you used last time", False),
    ]


def test_what_a_folder_would_get_in_words(tmp_path):
    show = make_series(tmp_path / "Library")
    movie = make_movie(tmp_path / "Library")
    both = "series,movie"
    assert folder_caption(folder_info(str(show)), show.name, both) == "would get a series note: Breaking Bad"
    assert folder_caption(folder_info(str(movie)), movie.name, both) == "would get a film note: The Matrix"
    assert folder_caption(folder_info(str(show)), show.name, "movie").startswith("a series folder (series notes are not ticked")
    assert folder_caption(folder_info(str(tmp_path / "Library")), "Library", both) == (
        "a library folder: the 1 series and 1 film folders below get their own notes")
    (movie / "about.md").write_text("mine\n", encoding="utf-8")
    assert folder_caption(folder_info(str(movie)), movie.name, both) == "already has about.md: left alone"
    (tmp_path / "Empty").mkdir()
    assert folder_caption(folder_info(str(tmp_path / "Empty")), "Empty", both).startswith("nothing to write a note for here")


def test_only_a_big_scope_gets_a_time_hint():
    assert scope_hint((10, 10), "series,movie") == "" and scope_hint(None, "series") == ""
    assert "About 300 folders: roughly" in scope_hint((200, 100), "series,movie")
    assert scope_hint((50, 400), "series") == ""  # only the series are counted when only series are ticked
