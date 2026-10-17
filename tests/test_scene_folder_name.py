"""Scene lookups (ThePornDB / StashDB) use the immediate parent folder's name when the file name says nothing (clip1,
scene 2, 001): the generic-name test, the folder pick, the prefill, the explanation in the status line, the
'Search with the folder name' box, the part-number preference and the 'Folder: X' header line."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

import core.scene_lookup as sl  # noqa: E402
from core.scene_lookup import (  # noqa: E402
    CATCH_ALL_FOLDERS, GENERIC_NAME_WORDS, is_generic_stem, parent_folder_candidate, part_marker,
    prefer_part_number, search_choice_for,
)
from core.video_file import VideoFile  # noqa: E402
from core.video_metadata import VideoMetadata  # noqa: E402
from gui.lookup_file_header import file_context  # noqa: E402
from gui.scene_search_dialog import SceneSearchDialog  # noqa: E402
from tests.test_scene_search_dialog import make_source, scene  # noqa: E402,F401

_app = QApplication.instance() or QApplication([])

FOLDER = "Strap Some Boyz #02"


# -- the generic-name test ---------------------------------------------------------------------------------------

def test_the_word_lists_are_the_decided_constants():
    assert GENERIC_NAME_WORDS == ("clip", "scene", "part", "pt", "cd", "disc", "disk", "video", "vid", "file", "track",
                                  "movie", "film", "sc")
    assert "ep" not in GENERIC_NAME_WORDS and "episode" not in GENERIC_NAME_WORDS  # TV is handled elsewhere
    assert {"downloads", "videos", "movies", "porn", "new folder"} <= set(CATCH_ALL_FOLDERS)


@pytest.mark.parametrize("stem", ["clip3", "Clip 3", "clip_03", "scene-2", "Scene.12", "part1", "pt2", "cd1", "disc 2",
                                  "video", "vid7", "file", "track 07", "movie", "film2", "sc4", "001", "7", "a12",
                                  "x5", "Her", "ab", "", "clip3b"[:5], "CLIP"])
def test_generic_stems(stem):
    assert is_generic_stem(stem)


@pytest.mark.parametrize("stem", ["Example.Studio.Title.2021", "Strap Some Boyz #02", "The Office S02E04", "Heat",
                                  "episode3", "S01E02", "Blue Movie Night", "partner"])
def test_real_names_are_not_generic(stem):
    assert not is_generic_stem(stem)


def test_part_marker():
    assert part_marker("clip3") == ("clip3", 3)
    assert part_marker("Scene 12") == ("Scene 12", 12)
    assert part_marker("movie") == ("movie", None)


# -- the parent folder pick -----------------------------------------------------------------------------------------

def test_the_immediate_parent_folder_is_used():
    name, text, year = parent_folder_candidate(f"D:/Videos/Studio/{FOLDER}/clip3.mp4")
    assert name == FOLDER and text == FOLDER and year == ""


def test_the_folder_year_is_kept():
    _name, text, year = parent_folder_candidate("D:/x/Some Title (2019)/clip1.mp4")
    assert text == "Some Title" and year == "2019"


@pytest.mark.parametrize("path", ["D:/clip3.mp4", "D:/x/Downloads/clip3.mp4", "D:/x/Videos/clip3.mp4",
                                  "D:/x/Movies/clip3.mp4", "D:/x/Porn/clip3.mp4", "D:/x/New folder/clip3.mp4",
                                  "D:/x/new_folder/clip3.mp4", "D:/x/clip 2/clip3.mp4", "D:/x/001/a.mp4",
                                  "D:/x/Part 1/clip3.mp4"])
def test_catch_all_generic_and_root_folders_are_skipped(path):
    assert parent_folder_candidate(path) == ("", "", "")


def test_only_the_immediate_parent_counts_not_its_parent():
    # the grandparent has the name but the immediate parent is a catch-all: no folder candidate at all
    assert parent_folder_candidate(f"D:/{FOLDER}/Downloads/clip3.mp4") == ("", "", "")


def test_search_choice_for_a_generic_and_a_real_name():
    generic = search_choice_for(f"D:/x/{FOLDER}/clip3.mp4")
    assert generic.generic and generic.use_folder and generic.part == "clip3" and generic.part_number == 3
    real = search_choice_for(f"D:/x/{FOLDER}/Example.Studio.Title.2021.mp4")
    assert not real.generic and real.has_folder and not real.use_folder
    assert real.text == "Example Studio Title" and real.year == "2021"  # unchanged behaviour for a real name
    no_folder = search_choice_for("D:/x/Downloads/clip3.mp4")
    assert no_folder.generic and not no_folder.has_folder and not no_folder.use_folder


def test_unchanged_behaviour_without_a_folder_candidate():
    assert sl.search_text_for("clip3") == ("clip3", "")  # the old function is untouched


# -- the part-number preference -----------------------------------------------------------------------------------------

def test_the_scene_with_the_part_number_goes_first_and_the_rest_keep_their_order():
    titles = ["Other One", "Strap Some Boyz Scene #13", "Strap Some Boyz Scene #03", "Another", "Part 3"]
    assert prefer_part_number(titles, 3) == [2, 4, 0, 1, 3]
    assert prefer_part_number(titles, None) == [0, 1, 2, 3, 4]
    assert prefer_part_number(["a", "b"], 3) == [0, 1]  # no match: nothing changes
    assert prefer_part_number(["Scene #13", "Scene #3"], 3) == [1, 0]  # 13 is not 3


# -- the dialog -----------------------------------------------------------------------------------------------------------

def make_window_file(window, tmp_path, folder, name):
    import shutil  # noqa: F401

    path = tmp_path / folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(os.urandom(200 * 1024))
    vf = VideoFile(path=path, metadata=VideoMetadata())
    window.video_files = [vf]
    window._refresh_table_rows()
    return vf


from tests.test_scene_search_dialog import window  # noqa: E402,F401


def test_a_generic_stem_prefills_the_folder_name_and_explains_it(window, tmp_path):
    vf = make_window_file(window, tmp_path, FOLDER, "clip3.mp4")
    source = make_source(by_title=[scene(match_kind="title", title="Strap Some Boyz Scene #03")])
    dialog = SceneSearchDialog(source, vf, parent=window)
    assert dialog.query_edit.text() == FOLDER
    assert dialog.folder_box is not None and dialog.folder_box.isChecked()
    assert source.calls["title"][-1][0] == FOLDER  # the title search ran with the folder name
    status = dialog.status_label.text()
    assert f'Searched for the folder name "{FOLDER}" because the file name "clip3" says nothing. Part: clip3.' in status
    assert dialog.query_edit.isEnabled()  # the box stays editable
    assert len(source.calls["hash"]) == 1  # the hash search still runs first, unchanged


def test_a_real_stem_is_unchanged_and_the_box_is_unchecked(window, tmp_path):
    vf = make_window_file(window, tmp_path, FOLDER, "Example.Studio.Title.2021.mp4")
    source = make_source(by_title=[scene(match_kind="title")])
    dialog = SceneSearchDialog(source, vf, parent=window)
    assert dialog.query_edit.text() == "Example Studio Title" and dialog.year_edit.text() == "2021"
    assert dialog.folder_box is not None and not dialog.folder_box.isChecked()
    assert source.calls["title"][-1][:2] == ("Example Studio Title", "2021")
    assert "folder name" not in dialog.status_label.text()


def test_no_checkbox_without_a_usable_folder(window, tmp_path):
    vf = make_window_file(window, tmp_path, "Downloads", "clip3.mp4")
    dialog = SceneSearchDialog(make_source(), vf, parent=window)
    assert dialog.folder_box is None and dialog.query_edit.text() == "clip3"


def test_the_checkbox_refills_the_box_and_searches_again(window, tmp_path):
    vf = make_window_file(window, tmp_path, FOLDER, "clip3.mp4")
    source = make_source(by_title=[scene(match_kind="title")])
    dialog = SceneSearchDialog(source, vf, parent=window)
    searches = len(source.calls["title"])
    dialog.folder_box.setChecked(False)
    assert dialog.query_edit.text() == "clip3" and len(source.calls["title"]) == searches + 1
    assert source.calls["title"][-1][0] == "clip3" and "folder name" not in dialog.status_label.text()
    dialog.folder_box.setChecked(True)
    assert dialog.query_edit.text() == FOLDER and len(source.calls["title"]) == searches + 2
    assert source.calls["title"][-1][0] == FOLDER and "Part: clip3" in dialog.status_label.text()


def test_the_scene_with_the_part_number_is_first_but_not_selected(window, tmp_path):
    vf = make_window_file(window, tmp_path, FOLDER, "clip3.mp4")
    found = [scene(match_kind="title", title="Strap Some Boyz Scene #01", external_id="1"),
             scene(match_kind="title", title="Strap Some Boyz Scene #03", external_id="3"),
             scene(match_kind="title", title="Strap Some Boyz Scene #02", external_id="2")]
    dialog = SceneSearchDialog(make_source(by_title=found), vf, parent=window)
    assert [r.external_id for r in dialog.results] == ["3", "1", "2"]
    assert dialog._selected() is None  # never auto-selected


def test_the_header_shows_the_folder_for_a_generic_name_only(window, tmp_path):
    vf = make_window_file(window, tmp_path, FOLDER, "clip3.mp4")
    context = file_context(vf)
    assert context.folder_name == FOLDER and context.searching_for == FOLDER
    dialog = SceneSearchDialog(make_source(), vf, parent=window)
    assert dialog.file_header.folder_label.text() == f"Folder: {FOLDER}"
    assert not dialog.file_header.folder_label.isHidden()
    real = make_window_file(window, tmp_path, FOLDER, "Example.Studio.Title.2021.mp4")
    assert file_context(real).folder_name == ""
    dialog2 = SceneSearchDialog(make_source(), real, parent=window)
    assert dialog2.file_header.folder_label.isHidden()
