"""The ThePornDB / StashDB lookup on the real MainWindow, offscreen, with a scratch settings file and a FAKE source (no
network): the hash search on open, the file-match highlight, the title flag, the explicit pick, the per-field overwrite
review before anything is written, Cancel, the missing-key panel, the source ids and the EMBEDDED poster (no sidecar
file is ever created)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtCore import QBuffer, QIODevice, Qt  # noqa: E402
from PyQt6.QtGui import QImage  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QHeaderView, QMessageBox  # noqa: E402

import gui.main_window as mw  # noqa: E402
import gui.scene_lookup_flow as flow  # noqa: E402
import gui.scene_search_dialog as ssd  # noqa: E402
from core import scene_lookup  # noqa: E402
from core.scene_lookup import MATCH_FILE, MATCH_TITLE, SceneLookupError, SceneResult  # noqa: E402
from core.video_file import VideoFile  # noqa: E402
from core.video_metadata import ContentType, VideoMetadata  # noqa: E402
from gui.lookup import CANCELLED  # noqa: E402
from gui.scene_search_dialog import SceneSearchDialog, SceneSource  # noqa: E402

_app = QApplication.instance() or QApplication([])


def scene(**over) -> SceneResult:
    base = dict(source="Fake", external_id="id-1", title="Test Scene Title", date="2021-11-15", studio="Example Studio",
                parent_studio="Example Network", performers=["Performer One", "Performer Two"], tags=["Tag A", "Tag B"],
                description="Synthetic description.", director="Director One", page_url="https://example.com/scenes/id-1",
                cover_url="https://cdn.example.com/c.jpg", poster_urls=["https://cdn.example.com/c.jpg"],
                match_kind=MATCH_FILE)
    base.update(over)
    return SceneResult(**base)


def tiny_png(width: int = 600, height: int = 900) -> bytes:
    """A generated solid image (synthetic pixels, not artwork) standing in for a downloaded picture; 600x900 is a
    poster, 900x600 a landscape picture."""
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(0x336699)
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


class FakeSource(SceneSource):
    """A SceneSource whose key is always there and whose calls are recorded."""

    def has_key(self) -> bool:
        return self.key_present


def make_source(by_hash=None, by_title=None, key=True, uses_md5=False, kinds=(), key_id="fake"):
    calls = {"hash": [], "title": [], "cover": []}

    def search_hash(hashes):
        calls["hash"].append(hashes)
        return list(by_hash or [])

    def search_title(text, year, kind):
        calls["title"].append((text, year, kind))
        return list(by_title or [])

    def download_cover(url):
        calls["cover"].append(url)
        return tiny_png()

    source = FakeSource(key_id=key_id, name="Fake", search_hash=search_hash, search_title=search_title,
                        download_cover=download_cover, uses_md5=uses_md5, kinds=kinds)
    source.key_present = key
    source.calls = calls
    return source


@pytest.fixture
def window(monkeypatch, tmp_path):
    import core.config as config

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")
    monkeypatch.setattr(mw.MainWindow, "_restore_last_folder_on_startup", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_check_external_tools_on_startup", lambda self: None)
    w = mw.MainWindow()
    path = tmp_path / "Example.Studio.Test.Scene.Title.2021.mp4"
    path.write_bytes(os.urandom(200 * 1024))  # over 128 KB, so it has an OSHASH
    w.video_files = [VideoFile(path=path, metadata=VideoMetadata())]
    w._refresh_table_rows()
    w.table.selectRow(0)
    yield w
    for vf in w.video_files:
        vf.dirty = False
    w.close()


@pytest.fixture
def no_message_boxes(monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text, *a, **k: shown.append(("warning", title, text)))
    monkeypatch.setattr(QMessageBox, "information", lambda parent, title, text, *a, **k: shown.append(("info", title, text)))
    return shown


def pick_first_and_accept(monkeypatch, cover=False, seen=None, landscape=False):
    def fake_exec(self):
        if seen is not None:
            seen.append(self)
        self.table.selectRow(0)
        self.landscape_box.setChecked(landscape)
        self.cover_box.setChecked(cover)
        self._on_accept()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(SceneSearchDialog, "exec", fake_exec)


def review(monkeypatch, accept=True, shown=None):
    from redactor_common.gui.overwrite_review_dialog import OverwriteReviewDialog

    def fake_exec(self):
        if shown is not None:
            shown.append(self)
        return QDialog.DialogCode.Accepted if accept else QDialog.DialogCode.Rejected

    monkeypatch.setattr(OverwriteReviewDialog, "exec", fake_exec)


# --- the dialog -------------------------------------------------------------------------------------------------------


def test_opening_runs_the_hash_search_and_highlights_the_file_match(window):
    source = make_source(by_hash=[scene()])
    dialog = SceneSearchDialog(source, window.video_files[0], parent=window)
    assert len(source.calls["hash"]) == 1
    hashes = source.calls["hash"][0]
    assert scene_lookup.valid_oshash(hashes.oshash) and hashes.md5 == ""
    assert source.calls["title"] == []  # a file match: the title search waits for the Search button
    assert dialog.table.rowCount() == 1
    assert dialog.table.item(0, ssd.COL_MATCH).text() == "File match"
    assert dialog.table.item(0, ssd.COL_TITLE).font().bold()
    assert dialog.table.item(0, ssd.COL_TITLE).background().style() != Qt.BrushStyle.NoBrush
    assert dialog.table.item(0, ssd.COL_TITLE).background().color().alpha() > 0
    # one exact file match is preselected (owner, 2026-10-17: Use Selected and the poster box were dead until a click)
    assert dialog.select_button.isEnabled() and dialog.cover_box.isEnabled() and dialog.selected_result is None
    assert dialog.detail.toPlainText()  # Use Selected still has to be pressed: nothing is applied by itself
    detail = dialog.detail.toPlainText()
    assert "File match" in detail and "Example Studio" in detail and "Performer One, Performer Two" in detail
    assert "match by title" not in detail


def test_the_search_box_is_prefilled_from_the_file_name_and_editable(window):
    dialog = SceneSearchDialog(make_source(), window.video_files[0], parent=window)
    assert dialog.query_edit.text() == "Example Studio Test Scene Title" and dialog.year_edit.text() == "2021"
    dialog.query_edit.setText("something else")
    dialog.year_edit.setText("")
    source = dialog.source
    dialog._on_search()
    assert source.calls["title"][-1] == ("something else", "", "")


def test_no_file_match_searches_by_title_and_flags_every_result(window):
    source = make_source(by_hash=[], by_title=[scene(match_kind=MATCH_TITLE), scene(external_id="id-2", title="Other", match_kind=MATCH_TITLE)])
    dialog = SceneSearchDialog(source, window.video_files[0], parent=window)
    assert len(source.calls["hash"]) == 1 and source.calls["title"][0][0] == "Example Studio Test Scene Title"
    assert dialog.table.rowCount() == 2
    assert dialog.table.item(0, ssd.COL_MATCH).text() == "Title: check it"
    assert dialog.table.item(0, ssd.COL_TITLE).background().style() == Qt.BrushStyle.NoBrush  # no file-match highlight
    assert "match by title: check it" in dialog.status_label.text()
    dialog.table.selectRow(1)
    assert "match by title: check it" in dialog.detail.toPlainText()


def test_the_md5_is_only_computed_when_preferences_say_so(window):
    source = make_source(by_hash=[], uses_md5=True)
    SceneSearchDialog(source, window.video_files[0], parent=window)
    assert source.calls["hash"][0].md5 == ""
    scene_lookup.set_md5_enabled(True)
    source2 = make_source(by_hash=[], uses_md5=True)
    SceneSearchDialog(source2, window.video_files[0], parent=window)
    assert len(source2.calls["hash"][0].md5) == 32
    source3 = make_source(by_hash=[], uses_md5=False)  # a service without MD5 never reads the whole file
    SceneSearchDialog(source3, window.video_files[0], parent=window)
    assert source3.calls["hash"][0].md5 == ""


def test_a_file_under_128_kb_goes_straight_to_the_title_search(window, tmp_path):
    small = tmp_path / "tiny.mp4"
    small.write_bytes(b"x" * 500)
    source = make_source(by_title=[scene(match_kind=MATCH_TITLE)])
    dialog = SceneSearchDialog(source, VideoFile(path=small, metadata=VideoMetadata()), parent=window)
    assert source.calls["hash"] == [] and len(source.calls["title"]) == 1
    assert "too small" in dialog.status_label.text()


def test_cancel_of_the_lookup_leaves_an_empty_dialog_and_no_error(window, monkeypatch, no_message_boxes):
    source = make_source(by_hash=[scene()])
    monkeypatch.setattr(ssd, "run_cancellable_lookup", lambda *a, **k: CANCELLED)
    dialog = SceneSearchDialog(source, window.video_files[0], parent=window)
    assert dialog.table.rowCount() == 0 and "cancelled" in dialog.status_label.text().lower()
    assert no_message_boxes == [] and source.calls["title"] == []


def test_a_service_error_is_shown_and_the_dialog_stays_usable(window, no_message_boxes):
    source = make_source()

    def boom(hashes):
        raise SceneLookupError("Could not reach Fake: down (check your internet connection).")

    source.search_hash = boom
    dialog = SceneSearchDialog(source, window.video_files[0], parent=window)
    assert no_message_boxes and "Could not reach Fake" in no_message_boxes[0][2]
    assert dialog.search_button.isEnabled() and dialog.table.rowCount() == 0


def test_without_a_key_the_dialog_says_where_to_get_one_and_asks_nothing(window, monkeypatch):
    opened = []
    source = make_source(by_hash=[scene()], key=False)
    source.key_id = "stashdb"
    dialog = SceneSearchDialog(source, window.video_files[0], parent=window, open_api_keys=lambda: opened.append(1))
    assert source.calls["hash"] == [] and source.calls["title"] == []
    assert "https://stashdb.org/register" in dialog.key_label.text() and "No StashDB API key" in dialog.key_label.text()
    assert not dialog.key_label.isHidden() and not dialog.key_button.isHidden() and not dialog.search_button.isEnabled()
    source.key_present = True  # the user saved a key in Tools > API Keys
    dialog.key_button.click()
    assert opened == [1] and dialog.search_button.isEnabled() and dialog.key_label.isHidden()
    assert len(source.calls["hash"]) == 1 and dialog.table.rowCount() == 1  # it searched right away


def test_the_results_table_headers_are_draggable(window):
    dialog = SceneSearchDialog(make_source(by_hash=[scene()]), window.video_files[0], parent=window)
    dialog.show()
    header = dialog.table.horizontalHeader()
    for section in range(header.count() - 1):
        assert header.sectionResizeMode(section) == QHeaderView.ResizeMode.Interactive
    dialog.close()


def test_the_dialog_shows_the_privacy_note(window):
    dialog = SceneSearchDialog(make_source(), window.video_files[0], parent=window)
    assert "Only the file's hash and/or the search text" in dialog.privacy_label.text()


# --- the flow: pick, review, write ----------------------------------------------------------------------------------------


def run(window, source):
    flow.run_scene_lookup(window, "fake", source_factory=lambda: source)


def test_no_pick_means_no_write(window, monkeypatch):
    source = make_source(by_hash=[scene()])
    monkeypatch.setattr(SceneSearchDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    run(window, source)
    vf = window.video_files[0]
    assert vf.metadata == VideoMetadata() and not vf.dirty and not window.undo_manager.can_undo()


def test_a_pick_on_a_blank_file_fills_the_fields_and_stages_them(window, monkeypatch):
    pick_first_and_accept(monkeypatch)
    source = make_source(by_hash=[scene()])
    run(window, source)
    meta = window.video_files[0].metadata
    assert (meta.title, meta.release_date, meta.studio, meta.collection, meta.cast, meta.genre_tags, meta.director) == (
        "Test Scene Title", "2021-11-15", "Example Studio", "Example Network", "Performer One, Performer Two",
        "Tag A, Tag B", "Director One")
    assert meta.description == "Synthetic description." and meta.content_type is ContentType.PORN
    assert "Fake: https://example.com/scenes/id-1" in meta.comment
    assert window.video_files[0].dirty  # staged: not on disk until Save
    assert window.undo_manager.can_undo() and source.calls["cover"] == []  # no cover unless asked
    window.undo_last_action()
    assert window.video_files[0].metadata == VideoMetadata()


def test_the_review_shows_before_an_existing_value_is_overwritten_and_starts_unticked(window, monkeypatch):
    window.video_files[0].metadata = VideoMetadata(title="My Own Title", studio="My Studio")
    pick_first_and_accept(monkeypatch)
    shown = []
    review(monkeypatch, accept=True, shown=shown)
    run(window, make_source(by_hash=[scene()]))
    assert len(shown) == 1  # the per-field review opened: two fields would overwrite something
    meta = window.video_files[0].metadata
    assert meta.title == "My Own Title" and meta.studio == "My Studio"  # untouched: they were not ticked
    assert meta.cast == "Performer One, Performer Two" and meta.director == "Director One"  # the blank ones were


def test_cancelling_the_review_changes_nothing_and_stages_no_poster(window, monkeypatch, tmp_path):
    window.video_files[0].metadata = VideoMetadata(title="My Own Title")
    pick_first_and_accept(monkeypatch, cover=True)
    review(monkeypatch, accept=False)
    source = make_source(by_hash=[scene()])
    run(window, source)
    vf = window.video_files[0]
    assert vf.metadata == VideoMetadata(title="My Own Title") and not vf.dirty and not window.undo_manager.can_undo()
    # The poster was fetched for the review to show it; nothing of it was staged or written anywhere.
    assert vf.metadata.cover_pending is None and not list(tmp_path.glob("*-poster.*"))


def listing(folder):
    return sorted(os.listdir(folder))


def test_the_poster_is_embedded_only_when_ticked_and_no_sidecar_is_created(window, monkeypatch, tmp_path):
    before = listing(tmp_path)
    pick_first_and_accept(monkeypatch, cover=False)
    source = make_source(by_hash=[scene()])
    run(window, source)
    vf = window.video_files[0]
    assert source.calls["cover"] == [] and vf.metadata.cover_pending is None  # not ticked: nothing downloaded

    vf.metadata = VideoMetadata()
    pick_first_and_accept(monkeypatch, cover=True)
    run(window, source)
    assert source.calls["cover"] == ["https://cdn.example.com/c.jpg"]
    assert vf.metadata.cover_pending[:3] == b"\xff\xd8\xff"  # the PNG the source returned became a JPEG, staged
    assert vf.dirty and "poster(s) will be embedded on Save" in window.status_bar.currentMessage()
    assert listing(tmp_path) == before  # NO sidecar file anywhere: nothing was written beside the video


def test_a_landscape_picture_is_not_embedded_unless_accepted(window, monkeypatch, tmp_path):
    before = listing(tmp_path)
    source = make_source(by_hash=[scene()])
    source.download_cover = lambda url: tiny_png(900, 600)
    pick_first_and_accept(monkeypatch, cover=True)
    run(window, source)
    vf = window.video_files[0]
    assert vf.metadata.cover_pending is None and vf.metadata.title == "Test Scene Title"
    assert "No poster available from Fake" in window.status_bar.currentMessage()
    assert listing(tmp_path) == before

    vf.metadata = VideoMetadata()
    pick_first_and_accept(monkeypatch, cover=True, landscape=True)
    source = make_source(by_hash=[scene(landscape_urls=["https://cdn.example.com/wide.jpg"])])
    source.download_cover = lambda url: tiny_png(900, 600)
    run(window, source)
    assert vf.metadata.cover_pending is not None  # the user accepted a landscape cover for this lookup


def test_a_result_without_a_poster_cannot_ask_for_one(window):
    dialog = SceneSearchDialog(make_source(by_hash=[scene(cover_url="", poster_urls=[])]), window.video_files[0], parent=window)
    dialog.table.selectRow(0)
    assert not dialog.cover_box.isEnabled()
    dialog.cover_box.setChecked(True)
    dialog._on_accept()
    assert dialog.save_cover is False
    assert "none from Fake" in dialog.detail.toPlainText()


def test_a_failed_poster_download_does_not_undo_the_metadata(window, monkeypatch, no_message_boxes):
    pick_first_and_accept(monkeypatch, cover=True)
    source = make_source(by_hash=[scene()])

    def boom(url):
        raise SceneLookupError("Could not download the cover: down.")

    source.download_cover = boom
    run(window, source)
    assert window.video_files[0].metadata.title == "Test Scene Title"
    assert window.video_files[0].metadata.cover_pending is None
    assert "No poster available from Fake" in window.status_bar.currentMessage()


def test_an_existing_cover_is_replaced_only_through_the_review(window, monkeypatch):
    vf = window.video_files[0]
    vf.metadata = VideoMetadata(cover_info="JPEG 600x900, 80 KB")
    pick_first_and_accept(monkeypatch, cover=True)
    shown = []
    review(monkeypatch, accept=True, shown=shown)
    run(window, make_source(by_hash=[scene()]))
    assert len(shown) == 1  # the existing cover is a conflict: the review appears
    # the poster row starts unticked (it would replace a cover): the accepted review left it out
    assert vf.metadata.cover_pending is None


def test_source_ids_are_staged_from_the_accepted_result(window, monkeypatch):
    uuid = "d284e2a0-789d-4e6c-beb9-6e61c24e8b84"
    result = scene(source="ThePornDB", external_id=uuid, page_url="https://theporndb.net/scenes/some-slug")
    pick_first_and_accept(monkeypatch)
    run(window, make_source(by_hash=[result]))
    md = window.video_files[0].metadata
    assert md.theporndb_id == uuid and md.stashdb_id == "" and md.tmdb_id == ""
    assert md.source_url == "https://theporndb.net/scenes/" + uuid
    assert md.metadata_match.startswith("theporndb;") and md.metadata_match.endswith(";file")


def test_nothing_is_staged_when_the_review_is_cancelled(window, monkeypatch):
    uuid = "d284e2a0-789d-4e6c-beb9-6e61c24e8b84"
    window.video_files[0].metadata = VideoMetadata(title="Mine")
    pick_first_and_accept(monkeypatch, cover=True)
    review(monkeypatch, accept=False)
    run(window, make_source(by_hash=[scene(source="ThePornDB", external_id=uuid)]))
    md = window.video_files[0].metadata
    assert md.theporndb_id == "" and md.cover_pending is None and md.title == "Mine"


def test_the_id_in_the_file_is_used_directly_without_a_search(window, monkeypatch):
    uuid = "d284e2a0-789d-4e6c-beb9-6e61c24e8b84"
    window.video_files[0].metadata = VideoMetadata(theporndb_id=uuid)
    asked = []

    def by_id(ref):
        asked.append(ref)
        return scene(source="ThePornDB", external_id=uuid, match_kind=MATCH_TITLE)

    source = make_source(by_hash=[scene()], by_title=[scene()], key_id="theporndb") if False else make_source(by_hash=[scene()])
    source.key_id = "theporndb"
    source.get_by_id = by_id
    source.id_kinds = ("scene", "movie", "jav")
    dialog = SceneSearchDialog(source, window.video_files[0], parent=window)
    assert [r.id for r in asked] == [uuid]
    assert source.calls["hash"] == [] and source.calls["title"] == []  # no search at all
    assert dialog.table.rowCount() == 1 and dialog.table.item(0, 0).text() == "File has an ID"
    assert "File has an ID" in dialog.status_label.text()
    dialog._on_accept()
    assert dialog.selected_result.is_id_match


def test_an_id_that_cannot_be_fetched_falls_back_to_the_normal_search(window):
    uuid = "d284e2a0-789d-4e6c-beb9-6e61c24e8b84"
    window.video_files[0].metadata = VideoMetadata(theporndb_id=uuid)

    def by_id(ref):
        raise SceneLookupError("gone")

    source = make_source(by_hash=[scene()])
    source.key_id, source.get_by_id = "theporndb", by_id
    SceneSearchDialog(source, window.video_files[0], parent=window)
    assert len(source.calls["hash"]) == 1


def test_several_selected_files_each_get_a_dialog_and_one_review(window, monkeypatch, tmp_path):
    second = tmp_path / "Other.File.mp4"
    second.write_bytes(os.urandom(200 * 1024))
    window.video_files.append(VideoFile(path=second, metadata=VideoMetadata(title="Existing Title")))
    window._refresh_table_rows()
    window.table.selectAll()
    seen = []
    pick_first_and_accept(monkeypatch, seen=seen)
    shown = []
    review(monkeypatch, accept=True, shown=shown)
    run(window, make_source(by_hash=[scene()]))
    assert len(seen) == 2 and len(shown) == 1  # a dialog per file, ONE review for the batch
    assert window.video_files[0].metadata.title == "Test Scene Title"
    assert window.video_files[1].metadata.title == "Existing Title"  # a conflict, unticked by default


def test_nothing_selected_says_so(window, no_message_boxes):
    window.table.clearSelection()
    run(window, make_source())
    assert no_message_boxes and no_message_boxes[0][1] == "No Files Selected"


# --- the menu ---------------------------------------------------------------------------------------------------------------


def test_menu_entries_open_the_flow_for_each_service(window, monkeypatch):
    calls = []
    monkeypatch.setattr(flow, "run_scene_lookup", lambda win, key: calls.append(key))
    menus = {a.text().replace("&", ""): a.menu() for a in window.menuBar().actions() if a.menu()}
    look_up = next(a for a in menus["Metadata"].actions() if a.menu()).menu()
    for action in look_up.actions():
        if action.text().replace("&", "") in ("ThePornDB…", "StashDB…"):
            assert not action.shortcut().toString()  # no shortcut, and certainly no Ctrl+Alt
            action.trigger()
    assert calls == ["theporndb", "stashdb"]


def test_the_real_sources_are_wired_to_the_clients(monkeypatch):
    from core import stashdb_client, theporndb_client

    seen = []
    monkeypatch.setattr(theporndb_client, "search_by_hash", lambda oshash: seen.append(("tp-hash", oshash)) or [])
    monkeypatch.setattr(theporndb_client, "search_by_title", lambda text, year, kind: seen.append(("tp-title", text, year, kind)) or [])
    monkeypatch.setattr(stashdb_client, "search_by_hash", lambda oshash, md5="": seen.append(("sd-hash", oshash, md5)) or [])
    monkeypatch.setattr(stashdb_client, "search_by_title", lambda text, year: seen.append(("sd-title", text, year)) or [])
    tp, sd = ssd.theporndb_source(), ssd.stashdb_source()
    tp.search_hash(scene_lookup.FileHashes(oshash="0123456789abcdef"))
    tp.search_title("a", "2021", "movie")
    sd.search_hash(scene_lookup.FileHashes(oshash="0123456789abcdef", md5="m"))
    sd.search_title("b", "", "")
    assert seen == [("tp-hash", "0123456789abcdef"), ("tp-title", "a", "2021", "movie"),
                    ("sd-hash", "0123456789abcdef", "m"), ("sd-title", "b", None)]
    assert [k for k, _label in tp.kinds] == ["scene", "movie", "jav"] and sd.kinds == () and sd.uses_md5 and not tp.uses_md5


def test_several_results_or_title_guesses_are_not_preselected(window):
    several = SceneSearchDialog(make_source(by_hash=[scene(), scene(external_id="id-2")]), window.video_files[0], parent=window)
    assert several.table.rowCount() == 2 and not several.select_button.isEnabled()
    guess = SceneSearchDialog(make_source(by_title=[scene(match_kind=MATCH_TITLE)]), window.video_files[0], parent=window)
    assert guess.table.rowCount() == 1 and not guess.select_button.isEnabled()


def test_a_result_without_a_poster_says_why_the_box_is_off(window):
    dialog = SceneSearchDialog(make_source(by_hash=[scene(cover_url="", poster_urls=[])]), window.video_files[0], parent=window)
    assert dialog.select_button.isEnabled() and not dialog.cover_box.isEnabled()
    assert "no portrait poster" in dialog.cover_box.text()


def test_batch_buttons_skip_leaves_the_file_cancel_cancels_the_lookup(window):
    from gui.lookup_file_header import file_context

    vf = window.video_files[0]
    skipped = SceneSearchDialog(make_source(), vf, parent=window, context=file_context(vf, 2, 4))
    assert [b.text() for b in (skipped.skip_button, skipped.select_button, skipped.cancel_button)] == [
        "Skip This File", "Use Selected", "Cancel"]
    skipped.skip_button.click()
    assert skipped.stop_requested is False and skipped.result() == QDialog.DialogCode.Rejected
    cancelled = SceneSearchDialog(make_source(), vf, parent=window, context=file_context(vf, 2, 4))
    cancelled.cancel_button.click()
    assert cancelled.stop_requested is True and cancelled.result() == QDialog.DialogCode.Rejected
    single = SceneSearchDialog(make_source(), vf, parent=window, context=file_context(vf))
    assert not hasattr(single, "skip_button")
