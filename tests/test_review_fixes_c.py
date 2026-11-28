"""Videoredactor review 2026-11-28, fix agent C: main window, tag panel, pickers and IMDb local (cards 10-14, 24, 33,
43, 57, 60, 62, 59, 71). Every test points the settings at a tmp path (through the shared `window` fixture)."""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtCore import QEvent, QItemSelectionModel, QPoint, QPointF, Qt  # noqa: E402
from PyQt6.QtGui import QWheelEvent  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox, QInputDialog  # noqa: E402

import gui.main_window as mw  # noqa: E402
from core import history  # noqa: E402
from core.video_file import VideoFile  # noqa: E402
from core.video_metadata import ContentType, VideoMetadata  # noqa: E402
from tests.test_batch_operations_and_undo import _app, _auto_accept, window  # noqa: E402,F401


def _select_rows_in_order(window, *rows):
    """Select the rows one after the other (Ctrl+click order), keeping the earlier ones."""
    window.table.clearSelection()
    model = window.table.model()
    flag = QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
    for row in rows:
        window.table.selectionModel().select(model.index(row, 0), flag)


# --- card 10: the wheel does not stage an edit ---------------------------------------------------


def test_wheel_over_panel_boxes_changes_nothing_and_stages_nothing(window):
    window.table.selectAll()
    _app.processEvents()
    panel = window.tag_panel
    panel.show_all_box.setChecked(True)
    _app.processEvents()
    spin = panel._inputs["season_number"]
    assert not panel.has_pending_changes()
    event = QWheelEvent(QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, 120), Qt.MouseButton.NoButton,
                        Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    before = spin.value()
    QApplication.sendEvent(spin, event)
    assert spin.value() == before
    assert not event.isAccepted()  # left for the scroll area, which scrolls the panel
    assert not panel.has_pending_changes()
    for name in ("content_type", "collected_from"):
        combo = panel._inputs.get(name)
        if combo is not None:
            index = combo.currentIndex()
            QApplication.sendEvent(combo, QWheelEvent(
                QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, -120), Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False))
            assert combo.currentIndex() == index
    assert not panel.has_pending_changes()


# --- card 11: numbering follows the table's rows -------------------------------------------------


def test_selected_and_visible_files_come_in_table_order(window):
    window.table.sortItems(0, Qt.SortOrder.DescendingOrder)
    _select_rows_in_order(window, 1, 0)  # Ctrl+click: second row first
    _app.processEvents()
    by_row = [window._file_for_row(r) for r in range(window.table.rowCount())]
    assert window._selected_video_files() == [by_row[0], by_row[1]]
    assert window._visible_video_files() == by_row


def test_number_episodes_counts_down_the_rows_shown(window, monkeypatch):
    window.table.sortItems(0, Qt.SortOrder.DescendingOrder)
    _select_rows_in_order(window, 1, 0)
    by_row = [window._file_for_row(r) for r in range(window.table.rowCount())]
    monkeypatch.setattr(mw, "prompt_and_generate_series_numbers", lambda *a, **k: ["1", "2"])
    window._quick_number_episodes(window._selected_video_files())
    assert [vf.metadata.episode_number for vf in by_row] == [1, 2]


# --- card 12: renames take the subtitle and poster along -----------------------------------------


def _lib(tmp_path):
    return tmp_path / "lib"  # the movie's own folder; the fixture's files live in tmp_path itself


def _names(tmp_path):
    return sorted(p.name for p in _lib(tmp_path).iterdir())


def _movie_with_sidecars(tmp_path, window):
    tmp_path = _lib(tmp_path)
    tmp_path.mkdir()
    video = tmp_path / "Movie (2001).mkv"
    video.write_bytes(b"")
    (tmp_path / "Movie (2001).en.srt").write_text("1\n", encoding="utf-8")
    (tmp_path / "Movie (2001)-poster.jpg").write_bytes(b"x")
    vf = VideoFile(path=video, metadata=VideoMetadata(title="New Name"))
    window.video_files = [vf]
    window._refresh_table_rows()
    return vf


def test_rename_by_pattern_moves_the_sidecars_and_undo_puts_them_back(window, tmp_path, monkeypatch):
    vf = _movie_with_sidecars(tmp_path, window)
    window.table.selectAll()
    _auto_accept(monkeypatch, mw.RenamePatternDialog, lambda d: d.pattern_edit.setText("%title%"))
    window._on_rename_by_pattern()
    assert vf.path.name == "New Name.mkv"
    assert _names(tmp_path) == ["New Name-poster.jpg", "New Name.en.srt", "New Name.mkv"]
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    window.undo_last_rename()
    assert _names(tmp_path) == ["Movie (2001)-poster.jpg", "Movie (2001).en.srt", "Movie (2001).mkv"]


def test_f2_rename_moves_the_sidecars_in_one_log_batch(window, tmp_path, monkeypatch):
    vf = _movie_with_sidecars(tmp_path, window)
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Renamed", True)))
    window.rename_single_file(vf)
    assert vf.path.name == "Renamed.mkv"
    assert _names(tmp_path) == ["Renamed-poster.jpg", "Renamed.en.srt", "Renamed.mkv"]
    batches = mw._rename_log()._read()
    assert len(batches) == 1 and len(batches[0].renames) == 3


def test_search_replace_filename_moves_the_sidecars(window, tmp_path, monkeypatch):
    vf = _movie_with_sidecars(tmp_path, window)
    window.table.selectAll()

    class FakeDialog:
        DialogCode = mw.SearchReplaceDialog.DialogCode

        def __init__(self, *a, **k):
            pass

        def exec(self):
            return self.DialogCode.Accepted

        def result_field_key(self):
            return mw.FILENAME_FIELD_KEY

        def accepted_changes(self):
            return {0: "Replaced"}

    monkeypatch.setattr(mw, "SearchReplaceDialog", FakeDialog)
    window._on_search_replace()
    assert vf.path.name == "Replaced.mkv"
    assert _names(tmp_path) == ["Replaced-poster.jpg", "Replaced.en.srt", "Replaced.mkv"]


def test_a_sidecar_that_cannot_move_is_reported_and_the_video_stays_renamed(window, tmp_path, monkeypatch):
    vf = _movie_with_sidecars(tmp_path, window)
    (_lib(tmp_path) / "Renamed.en.srt").write_text("taken", encoding="utf-8")  # the subtitle's new name is taken
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a[2])))
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("Renamed", True)))
    window.rename_single_file(vf)
    assert vf.path.name == "Renamed.mkv" and (_lib(tmp_path) / "Movie (2001).en.srt").exists()
    assert (_lib(tmp_path) / "Renamed.en.srt").read_text(encoding="utf-8") == "taken"
    assert warned and "Movie (2001).en.srt" in warned[0]


# --- card 13: Undo after Save ---------------------------------------------------------------------


def test_undo_after_save_leaves_the_row_unsaved(window):
    vf = window.video_files[0]
    vf.baseline = history.fields_text(vf.metadata)  # as loaded from disk
    window.table.selectRow(0)
    window._on_apply_to_selected({"title": "Brand New"})
    assert vf.dirty
    vf.baseline = history.fields_text(vf.metadata)  # what a successful Save does ...
    vf.dirty = False  # ... and the row is clean again
    window.undo_last_action()
    assert vf.metadata.title == "pilot"
    assert vf.dirty  # the old title is NOT what the file holds: Save All must still offer it
    window.redo_last_action()
    assert vf.metadata.title == "Brand New" and not vf.dirty  # redo is back to what the file holds


def test_undo_without_a_save_still_returns_to_clean(window):
    vf = window.video_files[0]
    vf.baseline = history.fields_text(vf.metadata)
    window.table.selectRow(0)
    window._on_apply_to_selected({"title": "Brand New"})
    window.undo_last_action()
    assert vf.metadata.title == "pilot" and not vf.dirty


# --- card 43: Remove from List asks about a typed edit --------------------------------------------


def test_remove_from_list_asks_about_a_typed_not_applied_edit(window, monkeypatch):
    asked = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(
        lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.No))
    window.table.selectAll()
    _app.processEvents()
    window.tag_panel._inputs["title"].setText("Typed, not applied")
    assert window.tag_panel.has_pending_changes()
    window._on_remove_from_list()
    assert len(asked) == 1 and "unsaved changes" in asked[0]
    assert len(window.video_files) == 2  # answered No: nothing removed, nothing lost
    assert all(vf.metadata.title == "Typed, not applied" for vf in window.video_files)


# --- card 62: the preview worker is stopped before a file operation -------------------------------


def test_release_previews_shuts_the_preview_loader_down_and_asks_again_afterwards(window, monkeypatch):
    window.table.selectRow(0)
    _app.processEvents()
    calls = []
    monkeypatch.setattr(window._preview_loader, "shutdown", lambda *a, **k: calls.append("shutdown") or True)
    monkeypatch.setattr(window._preview_loader, "cancel", lambda: calls.append("cancel"))
    monkeypatch.setattr(window._preview_loader, "request", lambda token, loader, *a: calls.append(("request", token)))
    window._release_previews([window.video_files[0].path])
    assert calls == ["shutdown"]
    window._refresh_table_rows()
    assert calls[-1] == ("request", window._selected_video_files()[0])
    calls.clear()
    window._refresh_table_rows()  # not stopped any more: no second request
    assert calls == []


# --- cards 24 and 57: IMDb local --------------------------------------------------------------------

from redactor_common.core.local_db import forget_cached  # noqa: E402

from core import imdb_local, imdb_settings  # noqa: E402
from tests.imdb_fixture import AKAS, BASICS, EPISODES, RATINGS, build  # noqa: E402
from tests.test_imdb_local import OFFICE, OFFICE_EPISODES, OFFICE_RATINGS  # noqa: E402


@pytest.fixture
def imdb(tmp_path):
    path, _s, _f = build(tmp_path, basics=BASICS + OFFICE, ratings=RATINGS + OFFICE_RATINGS,
                         episodes=EPISODES + OFFICE_EPISODES, akas=AKAS)
    yield path
    forget_cached(path)


def test_a_file_whose_imdb_id_is_an_episode_opens_on_its_series_in_tv_mode(window, monkeypatch, imdb):
    imdb_settings.save_database(imdb)
    vf = window.video_files[0]
    vf.path = vf.path.with_name("Harbor Lights (2005) S02E05.mkv")
    vf.metadata.imdb_id = "tt90000022"  # what an earlier episode import wrote
    vf.metadata.content_type = ContentType.TV
    vf.metadata.season_number = vf.metadata.episode_number = None
    window.table.clearSelection()
    window.table.selectRow(0)
    opened = []
    picked = []

    class Search:
        def __init__(self, mode, initial_query="", initial_year="", parent=None, source=None, existing=None,
                     context=None):
            opened.append((mode, existing[0].imdb_id if existing else None))
            self.selected_candidate = existing[0] if existing else None

        def exec(self):
            return True

    class Picker:
        def __init__(self, db_path, series, name, initial_season=None, initial_episode=None, parent=None,
                     context=None):
            picked.append((series, initial_season, initial_episode))
            self.selected_episode = imdb_local.find_episode(imdb_local.open_database(db_path), series,
                                                            initial_season, initial_episode)

        def exec(self):
            return True

    monkeypatch.setattr(mw, "TMDBSearchDialog", Search)
    import gui.imdb_episode_picker_dialog as picker_module
    monkeypatch.setattr(picker_module, "ImdbEpisodePickerDialog", Picker)
    window._on_import_imdb_local()  # used to raise AttributeError: an episode has no .title
    assert opened == [("tv", "tt90000021")]
    assert picked == [(90000021, 2, 5)]
    md = vf.metadata
    assert (md.show_title, md.title, md.season_number, md.episode_number) == ("Harbor Lights", "Moulting", 2, 5)


def test_an_episode_numbered_zero_keeps_its_number():
    zero = imdb_local.ImdbEpisodeInfo(episode_number=0, name="Pilot", overview="", season=1, air_date="2005")
    assert imdb_local.episode_fields(zero)["episode_number"] == 0
    unknown = imdb_local.ImdbEpisodeInfo(episode_number=0, name="No Number", overview="", air_date="", season=1, number_known=False)
    assert "episode_number" not in imdb_local.episode_fields(unknown)


def test_the_database_row_with_episode_zero_is_known_and_a_missing_number_is_not(imdb):
    zero = imdb_local._episode((90000050, 90000021, 1, 0, "Pilot", 2005, None, None, None))
    missing = imdb_local._episode((90000051, 90000021, 1, None, "Special", 2005, None, None, None))
    assert zero.number_known and not missing.number_known


# --- card 33: the episode picker is not shown after its seasons failed --------------------------------

from gui.episode_picker_dialog import EpisodePickerDialog, EpisodeSource  # noqa: E402


def test_a_picker_whose_seasons_failed_is_not_shown_by_exec(monkeypatch):
    class Boom(Exception):
        pass

    class Broken(EpisodeSource):
        errors = (Boom,)

        def seasons(self, owner):
            raise Boom("offline")

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    dialog = EpisodePickerDialog(Broken())
    shown = []
    monkeypatch.setattr(QDialog, "exec", lambda self: shown.append(True) or 1)
    assert dialog.exec() == 0 and not shown  # rejected at once; "Show Information Only" is never offered


def test_a_picker_with_no_seasons_is_not_shown_by_exec(monkeypatch):
    class Empty(EpisodeSource):
        def seasons(self, owner):
            return []

    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    dialog = EpisodePickerDialog(Empty())
    monkeypatch.setattr(QDialog, "exec", lambda self: pytest.fail("an empty picker must not be shown"))
    assert dialog.exec() == 0


# --- card 59: save, load and Export copy run on a worker thread -------------------------------------------


def _on_main() -> bool:
    import threading

    return threading.current_thread() is threading.main_thread()


def test_save_of_one_file_runs_on_a_worker_under_the_progress_dialog(window, monkeypatch):
    seen = []

    def fake_save(self, journal=True):
        seen.append(("save", _on_main()))
        self.save_error = ""
        self.dirty = False

    monkeypatch.setattr(VideoFile, "save", fake_save)
    dialogs = []
    real = mw.run_work_with_progress
    monkeypatch.setattr(mw, "run_work_with_progress",
                        lambda parent, total, label, work, **kw: dialogs.append((total, label)) or real(
                            parent, total, label, work, **kw))
    vf = window.video_files[0]
    vf.dirty = True
    window._save_files([vf])
    assert seen == [("save", False)]  # not on the GUI thread
    assert dialogs == [(1, "Saving files...")]  # a dialog even for one file
    assert "Saved 1" in window.status_bar.currentMessage() or "1" in window.status_bar.currentMessage()
    assert not vf.dirty


def test_an_unexpected_save_error_is_reported_on_the_gui_thread(window, monkeypatch):
    where = []

    def boom(self, journal=True):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(VideoFile, "save", boom)
    monkeypatch.setattr(mw.crash_dialog, "report_exception", lambda exc, text: where.append(_on_main()))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))
    vf = window.video_files[0]
    vf.dirty = True
    window._save_files([vf, window.video_files[1]])
    assert where == [True, True]  # the error box is made on the GUI thread, once per failed file
    assert vf.save_error == "unexpected error: disk on fire"


def test_loading_files_probes_them_on_a_worker_and_adds_them_on_the_gui_thread(window, monkeypatch, tmp_path):
    threads = []

    def fake_load(self):
        threads.append(_on_main())

    monkeypatch.setattr(VideoFile, "load", fake_load)
    folder = tmp_path / "more"
    folder.mkdir()
    paths = []
    for name in ("one.mkv", "two.mkv"):
        (folder / name).write_bytes(b"")
        paths.append(folder / name)
    window._add_files(paths)
    assert threads == [False, False]
    assert [vf.path.name for vf in window.video_files][-2:] == ["one.mkv", "two.mkv"]
    for vf in window.video_files:
        vf.dirty = False


def test_export_copy_is_chunked_and_cancel_removes_the_partial_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(mw, "_COPY_CHUNK", 4)
    src = tmp_path / "big.mkv"
    src.write_bytes(b"0123456789abcdef")
    dst = tmp_path / "out.mkv"
    mw._copy_no_clobber(str(src), str(dst))
    assert dst.read_bytes() == src.read_bytes()
    polls = []

    def cancel_after_two_chunks():
        polls.append(1)
        return len(polls) > 2

    other = tmp_path / "other.mkv"
    with pytest.raises(mw._CopyCancelled):
        mw._copy_no_clobber(str(src), str(other), cancel_after_two_chunks)
    assert not other.exists() and not (tmp_path / "other.mkv.copying").exists()  # nothing half-copied is left
    assert src.read_bytes() == b"0123456789abcdef"  # the original is untouched


def test_export_by_pattern_copies_on_a_worker_thread(window, tmp_path, monkeypatch):
    where = []
    real = mw._copy_no_clobber

    def spy(src, dst, should_cancel=None):
        where.append((_on_main(), should_cancel is not None))
        real(src, dst, should_cancel)

    monkeypatch.setattr(mw, "_copy_no_clobber", spy)
    vf = _movie_with_sidecars(tmp_path, window)
    window.table.selectAll()
    out = tmp_path / "exported"
    out.mkdir()
    monkeypatch.setattr(mw.RenamePatternDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(mw.RenamePatternDialog, "is_export_mode", lambda self: True)
    monkeypatch.setattr(mw.RenamePatternDialog, "is_move_mode", lambda self: False)
    monkeypatch.setattr(mw.RenamePatternDialog, "planned_renames",
                        lambda self: [(vf, str(vf.path), str(out / "Copy.mkv"))])
    window._on_rename_by_pattern()
    assert where == [(False, True)]
    assert (out / "Copy.mkv").exists() and vf.path.name == "Movie (2001).mkv"  # a copy: the original stays


# --- card 60: an embedded subtitle track is read off the GUI thread, once ---------------------------------


def test_embedded_subtitle_tracks_are_read_on_a_worker_thread_and_cached(tmp_path):
    import threading

    from core import subtitle_translation as st
    from gui import subtitle_translation_dialog as sd

    video = VideoFile(path=tmp_path / "film.mkv")
    video.path.write_bytes(b"")
    embedded = [st.SubtitleSource("Track 3 (English)", st.KIND_EMBEDDED, "en", str(video.path), 3),
                st.SubtitleSource("Track 4 (Russian)", st.KIND_EMBEDDED, "ru", str(video.path), 4)]
    srt = "1\n00:00:01,000 --> 00:00:02,000\nHello\n\n"
    reads = []

    def fake_read_text(source):
        reads.append((source.stream_index, threading.current_thread() is threading.main_thread()))
        return srt

    real = st.read_text
    st.read_text = fake_read_text
    try:
        dialog = sd.SubtitleTranslationDialog(video, mode=sd.MODE_COMPARE, sources=embedded)
        try:
            assert dialog.table.rowCount() == 1
            assert sorted(index for index, on_main in reads) == [3, 4]
            assert not any(on_main for _index, on_main in reads)  # ffmpeg ran on a worker, not the GUI thread
            dialog.second_combo.setCurrentIndex(0)  # a Compare combo change: both tracks are known already
            dialog.second_combo.setCurrentIndex(1)
            assert len(reads) == 2
        finally:
            dialog.close()
    finally:
        st.read_text = real


# --- card 71: Fix All repairs only the files of the confirmed plan -------------------------------------


def test_fix_all_repairs_and_sets_language_only_for_the_confirmed_files(window, tmp_path, monkeypatch):
    from gui import file_check_dialog as flow

    planned = VideoFile(path=tmp_path / "planned.mp4")
    late = VideoFile(path=tmp_path / "became_repairable.mp4")  # not in the plan: gains a finding after the remux
    calls = {"n": 0}

    def eligible(files):
        calls["n"] += 1
        # 1st call: the plan. Later calls (after the remux): one more file has become repairable.
        both = [planned] if calls["n"] == 1 else [planned, late]
        return {"remux": [planned], "cut": [], "repair": both, "language": both}

    repaired, languages = [], []
    monkeypatch.setattr(flow, "eligible_fixes", eligible)
    monkeypatch.setattr(flow, "remux_files", lambda w, files, dry_run=False: ([], [], False))
    monkeypatch.setattr(flow, "check_files", lambda *a, **k: None)
    monkeypatch.setattr(flow, "repair_files", lambda w, files: (repaired.extend(files) or list(files), [], []))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    import gui.track_language_dialog as language_dialog
    monkeypatch.setattr(language_dialog, "run_set_track_language", lambda w, files, details: languages.extend(files))
    flow._run_fix_all(window, [planned, late], lambda lines: "\n".join(lines))
    assert repaired == [planned]
    assert languages == [planned]
