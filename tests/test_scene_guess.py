"""The search guess for the ThePornDB / StashDB dialog (2026-10-17): a leading date or year, the studio (metadata, file
name, folder, grandparent), the title without release tags and scene numbers; the dialog's Studio box, ranking by
date and studio, the second pass with the studio in the text, the Movie default of the kind box, the sorted results
table still returning the right record, and the resizable layout."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import datetime  # noqa: E402

import pytest  # noqa: E402
from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication, QSplitter  # noqa: E402

import core.scene_lookup as sl  # noqa: E402
from core.scene_lookup import guess_search, known_studios_from, rank_results, split_leading_date  # noqa: E402
from core.video_metadata import VideoMetadata  # noqa: E402
from gui.scene_search_dialog import SceneSearchDialog  # noqa: E402
from tests.test_scene_folder_name import make_window_file  # noqa: E402
from tests.test_scene_search_dialog import make_source, scene, window  # noqa: E402,F401

_app = QApplication.instance() or QApplication([])


# -- the leading date ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text,date,year,rest", [
    ("2014-02-17 Strap Some Boyz", "2014-02-17", "2014", "Strap Some Boyz"),
    ("2014.02.17.Strap.Some.Boyz", "2014-02-17", "2014", "Strap.Some.Boyz"),
    ("14.02.17 Strap Some Boyz", "2014-02-17", "2014", "Strap Some Boyz"),
    ("140217 Strap Some Boyz", "2014-02-17", "2014", "Strap Some Boyz"),
    ("950217 Old One", "1995-02-17", "1995", "Old One"),
    ("2014 Strap Some Boyz", "", "2014", "Strap Some Boyz"),
    ("(2014) Strap Some Boyz", "", "2014", "Strap Some Boyz"),
    ("[2014-02-17] Strap Some Boyz", "2014-02-17", "2014", "Strap Some Boyz"),
])
def test_a_leading_date_or_year_is_split_off(text, date, year, rest):
    assert split_leading_date(text) == (date, year, rest)


@pytest.mark.parametrize("text", ["991399 Title", "2014-13-45 Title", "881231 Title", "Title 2014-02-17", "300 Spartans",
                                  "1984 Title", "23496 01 Title", "2014"])
def test_an_invalid_or_implausible_or_trailing_date_is_left_alone(text):
    assert split_leading_date(text) == ("", "", text)


def test_a_date_in_the_future_is_not_a_date():
    tomorrow = datetime.date.today() + datetime.timedelta(days=1)
    text = tomorrow.strftime("%y%m%d") + " Title"
    assert split_leading_date(text)[0] == ""


# -- the whole guess ----------------------------------------------------------------------------------------------

def test_date_in_the_name_and_the_studio_from_the_folder():
    g = guess_search("D:/lib/Evil Angel/2014-02-17 Strap Some Boyz 2 - Scene 3.mp4")
    assert (g.text, g.date, g.year, g.studio, g.studio_source, g.part_number) == (
        "Strap Some Boyz 2", "2014-02-17", "2014", "Evil Angel", "folder", 3)
    assert g.describe() == ('Searched for: "Strap Some Boyz 2" - studio from folder: Evil Angel - '
                            "date from file name: 2014-02-17")
    assert "Evil" not in g.text  # the studio is never in the search text


def test_a_studio_run_together_at_the_start_of_the_name_and_a_short_date():
    g = guess_search("D:/lib/misc stuff/EvilAngel.14.02.17.Strap.Some.Boyz.2.Scene.3.1080p.mp4")
    assert (g.text, g.date, g.studio, g.studio_source) == ("Strap Some Boyz 2", "2014-02-17", "Evil Angel", "file name")


def test_a_generic_file_name_takes_studio_and_title_from_a_studio_dash_title_folder():
    g = guess_search("D:/lib/Evil Angel - Strap Some Boyz 2 1080p/23496_01_1080p.mp4")
    assert (g.text, g.studio, g.studio_source, g.title_from_folder, g.part_number) == (
        "Strap Some Boyz 2", "Evil Angel", "folder", True, 1)


def test_a_name_without_a_date_has_no_date_or_year():
    g = guess_search("D:/lib/Some Studio Films/Strap Some Boyz 2 XXX 1080p WEB-DL.mp4")
    assert g.text == "Strap Some Boyz 2" and g.date == "" and g.year == "" and g.studio == ""
    assert g.describe() == 'Searched for: "Strap Some Boyz 2"'


def test_a_catch_all_folder_gives_no_studio_and_no_folder_title():
    g = guess_search("D:/lib/Movies/clip3.mp4")
    assert g.studio == "" and not g.title_from_folder and g.text == "clip3"
    g2 = guess_search("D:/lib/Movies/2014-02-17 Strap Some Boyz.mp4")
    assert g2.studio == "" and g2.text == "Strap Some Boyz" and g2.date == "2014-02-17"


def test_the_grandparent_folder_can_carry_the_studio():
    g = guess_search("D:/lib/Evil Angel/Strap Some Boyz 2/clip3.mp4")
    assert (g.studio, g.studio_source, g.text, g.title_from_folder) == ("Evil Angel", "parent folder", "Strap Some Boyz 2", True)


def test_the_studio_in_the_metadata_wins_over_every_folder():
    meta = VideoMetadata(studio="Metadata Studio")
    g = guess_search("D:/lib/Evil Angel/2014-02-17 Strap Some Boyz 2.mp4", meta)
    assert (g.studio, g.studio_source) == ("Metadata Studio", "metadata")


def test_a_session_studio_and_a_metadata_studio_split_a_folder_name():
    g = guess_search("D:/lib/Acme Films - Big Title (2019)/x01.mp4", known_studios=["Acme Films"])
    assert (g.studio, g.text, g.year) == ("Acme Films", "Big Title", "2019")
    meta = VideoMetadata(studio="Zed Pictures")
    g = guess_search("D:/lib/Zed Pictures - Another Title/x01.mp4", meta)
    assert (g.studio, g.studio_source, g.text) == ("Zed Pictures", "metadata", "Another Title")


def test_a_folder_that_is_only_a_studio_is_not_a_title():
    g = guess_search("D:/lib/Evil Angel/clip3.mp4")
    assert g.studio == "Evil Angel" and not g.title_from_folder


def test_known_studios_come_from_the_loaded_files_metadata():
    class F:
        def __init__(self, studio):
            self.metadata = VideoMetadata(studio=studio)

    assert known_studios_from([F("A Studio"), F("a studio"), F(""), F("B Studio")]) == ["A Studio", "B Studio"]
    assert known_studios_from(None) == []


def test_existing_release_name_parsing_is_unchanged():
    assert sl.search_text_for("Example.Studio.Title.2021") == ("Example Studio Title", "2021")


# -- ranking ------------------------------------------------------------------------------------------------------

def test_rank_results_date_then_studio_and_nothing_else_moves():
    plain = scene(external_id="p", date="2010-01-01", studio="Other", match_kind="title")
    studio = scene(external_id="s", date="2011-01-01", studio="Evil Angel", match_kind="title")
    dated = scene(external_id="d", date="2014-02-17", studio="Other", match_kind="title")
    both = scene(external_id="b", date="2014-02-17", studio="EvilAngel", match_kind="title")
    exact = scene(external_id="f", date="1999-01-01", studio="Other")  # a file match stays on top
    ranked = rank_results([plain, studio, dated, both, exact], "2014-02-17", "Evil Angel")
    assert [r.external_id for r in ranked] == ["f", "b", "d", "s", "p"]
    assert [r.external_id for r in rank_results([plain, studio], "", "")] == ["p", "s"]


# -- the dialog ----------------------------------------------------------------------------------------------------

def _file(window, tmp_path, folder="Evil Angel", name="2014-02-17 Strap Some Boyz 2 - Scene 3.mp4"):
    return make_window_file(window, tmp_path, folder, name)


def test_the_boxes_the_status_line_and_the_marks(window, tmp_path):
    vf = _file(window, tmp_path)
    found = [scene(external_id="o", title="Other", date="2010-01-01", studio="Else", match_kind="title"),
             scene(external_id="s", title="Same Studio", date="2012-01-01", studio="Evil Angel", match_kind="title"),
             scene(external_id="d", title="Right Day", date="2014-02-17", studio="Else", match_kind="title"),
             scene(external_id="x", title="More", date="2013-01-01", studio="Else", match_kind="title"),
             scene(external_id="y", title="Even More", date="2013-02-01", studio="Else", match_kind="title"),
             ]
    source = make_source(by_title=found)
    dialog = SceneSearchDialog(source, vf, parent=window)
    assert (dialog.query_edit.text(), dialog.year_edit.text(), dialog.studio_edit.text()) == (
        "Strap Some Boyz 2", "2014", "Evil Angel")
    assert source.calls["title"][0][:2] == ("Strap Some Boyz 2", "2014")  # the studio is NOT in the first query
    assert len(source.calls["title"]) == 1  # five results: no second pass
    assert [r.external_id for r in dialog.results][:2] == ["d", "s"]
    assert "date matches" in dialog.table.item(0, ssd_col("match")).text()
    assert "studio matches" in dialog.table.item(1, ssd_col("match")).text()
    status = dialog.status_label.text()
    assert 'Searched for: "Strap Some Boyz 2"' in status and "studio from folder: Evil Angel" in status
    assert "date from file name: 2014-02-17" in status


def ssd_col(name):
    import gui.scene_search_dialog as ssd

    return {"match": ssd.COL_MATCH, "title": ssd.COL_TITLE, "studio": ssd.COL_STUDIO, "date": ssd.COL_DATE}[name]


def test_the_second_pass_adds_the_studio_only_when_the_title_alone_found_little(window, tmp_path):
    vf = _file(window, tmp_path)
    queries = []
    source = make_source()

    def search_title(text, year, kind):
        queries.append(text)
        if text.startswith("Evil Angel"):
            return [scene(external_id="b", title="Strap Some Boyz 2", match_kind="title")]
        return [scene(external_id="a", title="Unrelated", match_kind="title")]

    source.search_title = search_title
    dialog = SceneSearchDialog(source, vf, parent=window)
    assert queries == ["Strap Some Boyz 2", "Evil Angel Strap Some Boyz 2"]
    assert sorted(r.external_id for r in dialog.results) == ["a", "b"]
    assert "searched again with the studio" in dialog.status_label.text()


def test_no_second_pass_without_a_studio_or_with_a_file_match_or_when_it_is_already_there(window, tmp_path):
    vf = _file(window, tmp_path, folder="Downloads", name="Strap Some Boyz 2.mp4")
    source = make_source(by_title=[])
    SceneSearchDialog(source, vf, parent=window)
    assert len(source.calls["title"]) == 1  # no studio at all
    vf = _file(window, tmp_path)
    source = make_source(by_title=[scene(match_kind="file match")])
    SceneSearchDialog(source, vf, parent=window)
    assert len(source.calls["title"]) == 1  # a file match is enough
    assert not sl.wants_studio_pass([], "Evil Angel", "Evil Angel Strap")


def test_the_studio_box_is_editable_reused_and_empty_means_none(window, tmp_path):
    vf = _file(window, tmp_path)
    source = make_source(by_title=[])
    dialog = SceneSearchDialog(source, vf, parent=window)
    source.calls["title"].clear()
    dialog.studio_edit.setText("")
    dialog._on_search()
    assert [c[0] for c in source.calls["title"]] == ["Strap Some Boyz 2"]  # no studio, no second pass
    source.calls["title"].clear()
    dialog.studio_edit.setText("Other Studio")
    dialog._on_search()
    assert [c[0] for c in source.calls["title"]] == ["Strap Some Boyz 2", "Other Studio Strap Some Boyz 2"]
    assert "studio: Other Studio (edited)" in dialog.status_label.text()


def test_try_with_studio_searches_with_the_studio_in_the_text(window, tmp_path):
    vf = _file(window, tmp_path)
    source = make_source(by_title=[scene(match_kind="title") for _ in range(6)])
    dialog = SceneSearchDialog(source, vf, parent=window)
    source.calls["title"].clear()
    dialog.studio_button.click()
    assert [c[0] for c in source.calls["title"]] == ["Evil Angel Strap Some Boyz 2"]


def test_the_metadata_studio_prefills_the_box(window, tmp_path):
    vf = _file(window, tmp_path, folder="Whatever")
    vf.metadata.studio = "Meta Studio"
    dialog = SceneSearchDialog(make_source(), vf, parent=window)
    assert dialog.studio_edit.text() == "Meta Studio" and "studio from metadata" in dialog.status_label.text()


def test_the_studios_of_the_loaded_files_are_known(window, tmp_path):
    other = _file(window, tmp_path, folder="Elsewhere", name="a.mp4")
    other.metadata.studio = "Acme Films"
    vf = make_window_file(window, tmp_path, "Acme Films", "2020-05-01 Big Title.mp4")
    window.video_files = [other, vf]
    dialog = SceneSearchDialog(make_source(), vf, parent=window)
    assert dialog.studio_edit.text() == "Acme Films" and dialog.query_edit.text() == "Big Title"


def test_the_folder_checkbox_still_toggles_title_and_studio(window, tmp_path):
    vf = make_window_file(window, tmp_path, "Evil Angel - Strap Some Boyz 2 1080p", "23496_01_1080p.mp4")
    source = make_source(by_title=[scene(match_kind="title")])
    dialog = SceneSearchDialog(source, vf, parent=window)
    assert dialog.query_edit.text() == "Strap Some Boyz 2" and dialog.studio_edit.text() == "Evil Angel"
    assert dialog.folder_box.isChecked()
    dialog.folder_box.setChecked(False)
    assert dialog.query_edit.text() == "23496 01"
    dialog.folder_box.setChecked(True)
    assert dialog.query_edit.text() == "Strap Some Boyz 2"


# -- the kind box ---------------------------------------------------------------------------------------------------

KINDS = (("scene", "Scene"), ("movie", "Movie"), ("jav", "JAV"))


def test_the_kind_defaults_to_movie_and_a_deliberate_choice_is_remembered_per_source(window, tmp_path):
    vf = _file(window, tmp_path)
    source = make_source(by_title=[scene(match_kind="title")], kinds=KINDS)
    dialog = SceneSearchDialog(source, vf, parent=window)
    assert dialog.kind_combo.currentData() == "movie" and source.calls["title"][0][2] == "movie"
    dialog.kind_combo.setCurrentIndex(0)
    dialog.kind_combo.activated.emit(0)  # what a click on the drop-down sends
    again = SceneSearchDialog(make_source(kinds=KINDS), vf, parent=window)
    assert again.kind_combo.currentData() == "scene"
    other_source = make_source(kinds=KINDS, key_id="another")
    assert SceneSearchDialog(other_source, vf, parent=window).kind_combo.currentData() == "movie"


def test_the_hash_search_does_not_depend_on_the_kind(window, tmp_path):
    vf = _file(window, tmp_path)
    source = make_source(by_hash=[scene()], kinds=KINDS)
    dialog = SceneSearchDialog(source, vf, parent=window)
    assert len(source.calls["hash"]) == 1 and source.calls["title"] == []
    assert dialog.results[0].is_file_match


def test_a_source_without_kinds_has_no_kind_box(window, tmp_path):
    dialog = SceneSearchDialog(make_source(), _file(window, tmp_path), parent=window)
    assert dialog.kind_combo is None


def test_a_movie_record_with_only_title_studio_and_date_still_shows_and_maps(window, tmp_path):
    from core.scene_lookup import scene_fields
    from core.theporndb_client import parse_scene

    movie = parse_scene({"id": "m1", "type": "Movie", "title": "Strap Some Boyz 2", "date": "2014-02-17",
                         "site": {"name": "Evil Angel"},
                         "scenes": [{"performers": [{"name": "P One"}, {"name": "P Two"}]},
                                    {"performers": [{"name": "P Two"}, {"name": "P Three"}]}]}, "title match", "movie")
    assert movie.kind == "movie" and movie.page_url.endswith("/movies/m1")
    assert movie.performers == ["P One", "P Two", "P Three"]  # collected from the scenes inside the movie
    fields = scene_fields(movie)
    assert (fields["title"], fields["studio"], fields["release_date"]) == ("Strap Some Boyz 2", "Evil Angel", "2014-02-17")
    assert fields["cast"] == "P One, P Two, P Three"
    bare = parse_scene({"id": "m2", "type": "Movie", "title": "Bare"}, "title match", "movie")  # nothing but a title
    assert scene_fields(bare)["title"] == "Bare"
    dialog = SceneSearchDialog(make_source(by_title=[movie, bare], kinds=KINDS), _file(window, tmp_path), parent=window)
    texts = [[dialog.table.item(r, c).text() for c in (1, 2, 3, 4)] for r in range(2)]
    assert ["Strap Some Boyz 2", "Evil Angel", "2014-02-17", "P One, P Two, P Three"] in texts
    dialog.table.selectRow(0)
    assert "Strap Some Boyz 2" in dialog.detail.toPlainText() or "Bare" in dialog.detail.toPlainText()


def test_the_kind_reaches_the_real_clients(monkeypatch):
    import core.theporndb_client as tpdb

    seen = []
    monkeypatch.setattr(tpdb, "_get_json", lambda path, params=None, **k: seen.append(path) or {"data": []})
    from gui.scene_search_dialog import theporndb_source

    theporndb_source().search_title("x", "", "movie")
    theporndb_source().search_title("x", "", "")
    assert seen == ["/movies", "/scenes"]


# -- sorting, mapping and the layout --------------------------------------------------------------------------------

def test_sorting_the_results_then_using_the_first_visual_row_returns_that_result(window, tmp_path):
    vf = _file(window, tmp_path, folder="Downloads", name="Strap Some Boyz 2.mp4")
    found = [scene(external_id="1", title="Charlie", date="2012-01-01", studio="S1", match_kind="title"),
             scene(external_id="2", title="Alpha 10", date="2014-01-01", studio="S2", match_kind="title"),
             scene(external_id="3", title="Alpha 2", date="2013-01-01", studio="S3", match_kind="title")]
    dialog = SceneSearchDialog(make_source(by_title=found), vf, parent=window)
    assert dialog.table.horizontalHeader().isSortIndicatorShown() and dialog.table.isSortingEnabled()
    dialog.table.sortByColumn(ssd_col("title"), Qt.SortOrder.AscendingOrder)
    assert [dialog.table.item(r, ssd_col("title")).text() for r in range(3)] == ["Alpha 2", "Alpha 10", "Charlie"]
    dialog.table.selectRow(0)
    assert dialog._selected().external_id == "3"
    dialog.table.sortByColumn(ssd_col("date"), Qt.SortOrder.DescendingOrder)
    dialog.table.selectRow(0)
    assert dialog._selected().external_id == "2"
    dialog._on_accept()
    assert dialog.selected_result.external_id == "2"


def test_the_results_and_the_detail_share_a_visible_splitter_and_the_layout_is_remembered(window, tmp_path):
    vf = _file(window, tmp_path)
    dialog = SceneSearchDialog(make_source(by_title=[scene(match_kind="title")]), vf, parent=window)
    splitter = dialog.splitter
    assert isinstance(splitter, QSplitter) and splitter.orientation() == Qt.Orientation.Vertical
    assert splitter.widget(0) is dialog.table and splitter.widget(1) is dialog.detail
    assert splitter.handleWidth() >= 6 and not splitter.childrenCollapsible()
    dialog.resize(900, 700)
    dialog.show()
    splitter.setSizes([300, 200])
    dialog.done(0)
    from core.config import get_setting

    assert get_setting("dialog_layout", "scene_search_size") == "900x700"
    first, second = (int(n) for n in get_setting("dialog_layout", "scene_search_split").split(","))
    assert first > second  # the 300:200 shape is kept (Qt scales it to the room there is)
    again = SceneSearchDialog(make_source(), vf, parent=window)
    assert (again.width(), again.height()) == (900, 700)
