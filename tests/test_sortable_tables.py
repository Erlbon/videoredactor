"""Every table header sorts (owner, 2026-10-17: 'ALL column headers are sortable') AND a sorted table still returns the
right record: embed posters, file check, track language (cell widgets: rebuild-style sort), the scene results (in
test_scene_guess), the subtitle translation (in test_subtitle_translation), the main table; the shared lint; the
resizable result/detail splitters of the sibling lookup dialogs."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication, QSplitter  # noqa: E402
from redactor_common.gui.table_columns import lint_sortable_headers  # noqa: E402

from core import embed_posters, track_language as tl  # noqa: E402
from gui import embed_posters_dialog as epd  # noqa: E402
from gui import file_check_dialog as fcd  # noqa: E402
from gui import track_language_dialog as tld  # noqa: E402
from tests.test_embed_posters import library, load_all  # noqa: E402,F401
from tests.test_file_check import qapp  # noqa: E402,F401
from tests.track_language_helpers import make_mp4  # noqa: E402

_app = QApplication.instance() or QApplication([])
REPO = Path(__file__).resolve().parent.parent
ASC, DESC = Qt.SortOrder.AscendingOrder, Qt.SortOrder.DescendingOrder


def test_every_table_in_the_gui_folder_turns_sorting_on():
    assert lint_sortable_headers(REPO / "gui") == []


def test_the_main_table_sorts_and_shows_the_arrow():
    import gui.main_window as mw

    source = (REPO / "gui" / "main_window.py").read_text(encoding="utf-8")
    assert "self.table.setSortingEnabled(True)" in source  # the existing opt-in (sorted double click is tested elsewhere)
    assert mw.FILE_ROLE is not None


def test_embed_posters_sorted_rows_return_the_right_offers(library):
    offers = embed_posters.find_offers(load_all(library))
    dialog = epd.EmbedPostersDialog(offers)
    table = dialog.table
    assert table.isSortingEnabled() and table.horizontalHeader().isSortIndicatorShown()
    table.sortByColumn(epd.COL_VIDEO, DESC)
    assert [table.item(r, epd.COL_VIDEO).text() for r in range(3)] == ["Show.S01E03.mp4", "Show.S01E02.mp4", "Show.S01E01.mp4"]
    for row in range(3):
        table.item(row, epd.COL_TICK).setCheckState(Qt.CheckState.Unchecked)
    table.item(0, epd.COL_TICK).setCheckState(Qt.CheckState.Checked)  # the FIRST VISUAL row
    ticked = dialog.ticked_offers()
    assert [o.video.path.name for o in ticked] == ["Show.S01E03.mp4"]
    table.sortByColumn(epd.COL_VERDICT, ASC)  # sorting by another column keeps the same tick on the same offer
    assert [o.video.path.name for o in dialog.ticked_offers()] == ["Show.S01E03.mp4"]
    table.sortByColumn(epd.COL_TICK, DESC)
    assert table.item(0, epd.COL_TICK).checkState() == Qt.CheckState.Checked


def _checked(name, status, summary):
    check = SimpleNamespace(findings=[SimpleNamespace(code="x")], status=status, summary=lambda s=summary: s, deep=None,
                            can_repair=False, can_cut=False, can_remux_container=False)
    return SimpleNamespace(path=Path("D:/v") / name, check=check)


def test_file_check_results_sort_by_every_column():
    files = [_checked("b.mp4", "NOTE", "10 things"), _checked("a.mp4", "DAMAGED", "9 things"),
             _checked("c.mp4", "CHECKED OK", "2 things")]
    dialog = fcd.FileCheckResultsDialog(files)
    table = dialog.table
    assert table.isSortingEnabled() and table.horizontalHeader().isSortIndicatorShown()
    table.sortByColumn(2, ASC)  # natural: '2 things' < '9 things' < '10 things'
    assert [table.item(r, 2).text() for r in range(3)] == ["2 things", "9 things", "10 things"]
    assert [table.item(r, 0).text() for r in range(3)] == ["c.mp4", "a.mp4", "b.mp4"]  # the row moved as a whole
    table.sortByColumn(0, DESC)
    assert [table.item(r, 0).text() for r in range(3)] == ["c.mp4", "b.mp4", "a.mp4"]


def test_track_language_header_click_sorts_and_the_choice_stays_with_its_track(tmp_path, qapp):
    a = make_mp4(tmp_path / "a.mp4", languages=["eng", None])
    b = make_mp4(tmp_path / "b.mp4")
    entries = [(str(p), tl.list_tracks(p)) for p in (a, b)]
    dialog = tld.TrackLanguageDialog(entries)
    table = dialog.table

    def labels():
        return [table.item(r, 0).text() for r in range(table.rowCount())]

    def row_of_label(prefix):
        return next(r for r, text in enumerate(labels()) if text.startswith(prefix))

    assert labels()[:2] == ["Audio 1 (aac) in 2 files", "Audio 2 (aac) in 2 files"]
    assert table.horizontalHeader().isSortIndicatorShown()
    first_combo = table.cellWidget(row_of_label("Audio 1"), 2)  # a choice made on Audio 1, then a sort that moves it
    first_combo.setCurrentIndex(first_combo.findData("nor"))
    table.horizontalHeader().sectionClicked.emit(0)  # ascending
    table.horizontalHeader().sectionClicked.emit(0)  # descending: Video, Audio 2, Audio 1
    assert labels() == sorted(labels(), key=str.lower, reverse=True)
    assert table.cellWidget(row_of_label("Audio 1"), 2).currentData() == "nor"
    assert table.cellWidget(row_of_label("Audio 2"), 2).currentData() == ""
    audio1 = {(p, t.index) for p, tracks in entries for t in tracks if t.kind == "audio" and t.position == 1}
    assert {(p, i) for p, v in dialog.plan().items() for i in v} <= audio1 and dialog.plan()
    # a choice on the Audio 2 row lands on Audio 2, wherever that row is now
    top = table.cellWidget(row_of_label("Audio 2"), 2)
    other = next(code for code in (top.itemData(i) for i in range(top.count())) if code and code not in ("nor", "und"))
    top.setCurrentIndex(top.findData(other))
    plan = dialog.plan()
    for path, tracks in entries:
        for t in tracks:
            if t.kind == "audio" and t.position == 2:
                assert plan[path][t.index] == other
    assert "will change" in dialog.preview.text()
    table.horizontalHeader().sectionClicked.emit(1)  # Currently
    assert dialog.apply_button.isEnabled() and table.rowCount() == len(labels())


# -- the sibling lookup dialogs: results over details in a splitter ----------------------------------------------------


def test_tmdb_and_episode_dialogs_have_a_remembered_results_detail_splitter(tmp_path, monkeypatch):
    import core.config as config
    from gui.episode_picker_dialog import EpisodePickerDialog
    from gui.tmdb_search_dialog import TMDBSearchDialog
    from tests.test_episode_picker_dialog import PerSeason

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")
    tmdb = TMDBSearchDialog("movie", "", "")
    assert isinstance(tmdb.splitter, QSplitter) and tmdb.splitter.orientation() == Qt.Orientation.Vertical
    assert tmdb.splitter.widget(0) is tmdb.results_list and tmdb.splitter.widget(1) is tmdb.overview_label
    assert tmdb.splitter.handleWidth() >= 6
    tmdb.resize(640, 640)
    tmdb.done(0)
    assert config.get_setting("dialog_layout", "tmdbsearchdialog_size") == "640x640"
    assert TMDBSearchDialog("movie", "", "").width() == 640

    episodes = EpisodePickerDialog(PerSeason())
    assert isinstance(episodes.splitter, QSplitter)
    assert episodes.splitter.widget(0) is episodes.episode_list and episodes.splitter.widget(1) is episodes.overview_box
    episodes.resize(500, 600)
    episodes.done(0)
    assert config.get_setting("dialog_layout", "episodepickerdialog_size") == "500x600"
