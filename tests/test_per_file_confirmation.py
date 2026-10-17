"""Nothing is written to a file until THAT file is confirmed (2026-10-17, the owner's TV import bug): the TMDB TV import
used to apply the show-level fields, the show poster and the TMDB show id to EVERY selected file before the per-file
episode question, so a file the owner skipped still got them. Now each file gets one of three explicit choices
(Use This Episode / Show Information Only / Skip This File; in a batch Cancel, Esc and X cancel the whole lookup) and a skipped file stays COMPLETELY
untouched. The same scenario for the IMDb (local) TV import, the TheTVDB import and the ThePornDB/StashDB lookup, the
Stop Here button, undo, and the header every lookup dialog shows (file x of N, full name, what was parsed, ids it
already has). Real MainWindow offscreen, fake clients, real MP4s for the Save read-back."""

import copy
import hashlib
import os
import shutil

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

import gui.main_window as mw  # noqa: E402
from core import tmdb_client  # noqa: E402
from core.video_file import VideoFile  # noqa: E402
from core.video_metadata import ContentType, VideoMetadata  # noqa: E402
from gui.episode_picker_dialog import CHOICE_EPISODE, CHOICE_SHOW_ONLY, CHOICE_SKIP, EpisodePickerDialog, EpisodeSource  # noqa: E402
from gui.lookup_file_header import LookupFileHeader, file_context, searching_for_text  # noqa: E402
from tests.sample_clip import SAMPLE, SKIP_REASON  # noqa: E402
from tests.test_poster import solid  # noqa: E402

_app = QApplication.instance() or QApplication([])

SHOW = {"show_title": "Breaking Bad", "description": "Show plot.", "release_date": "2008-01-20", "network": "AMC",
        "genre_tags": "Drama", "_poster_path": "/p.jpg", "_imdb_id": "tt0903747", "_tvdb_id": 81189}


@pytest.fixture(autouse=True)
def boxes(monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text, *a, **k: shown.append(("warning", title, text)))
    monkeypatch.setattr(QMessageBox, "information", lambda parent, title, text, *a, **k: shown.append(("info", title, text)))
    return shown


def make_window(monkeypatch, tmp_path, names, real=False):
    import core.config as config

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")
    monkeypatch.setattr(mw.MainWindow, "_restore_last_folder_on_startup", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_check_external_tools_on_startup", lambda self: None)
    w = mw.MainWindow()
    files = []
    for name in names:
        path = tmp_path / name
        if real:
            shutil.copy(SAMPLE, path)
            vf = VideoFile(path=path)
            vf.load()
        else:
            path.write_bytes(b"x" * 1000)
            vf = VideoFile(path=path, metadata=VideoMetadata())
        files.append(vf)
    w.video_files = files
    w._refresh_table_rows()
    w.table.selectAll()
    return w


def close(w):
    for vf in w.video_files:
        vf.dirty = False
    w.close()


def state(vf):
    return copy.deepcopy(vf.metadata), vf.dirty


def digest(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


class ShowDialog:
    """Stands in for the TMDB / TheTVDB show search dialog: picks one show."""

    candidate = tmdb_client.TVCandidate(1396, "Breaking Bad", "2008-01-20", "", "/p.jpg")

    def __init__(self, *a, **k):
        self.selected_candidate = type(self).candidate
        self.save_poster, self.accept_landscape, self.selected_how = True, False, "title"
        self.kwargs = k

    def exec(self):
        return True


class Script:
    """The episode picker's answers by file name: 'episode' (the number is the file's E number), 'show', 'skip',
    'esc' (the window is closed) or 'stop' (Stop Here)."""

    def __init__(self, answers):
        self.answers = answers
        self.seen = []  # (file name, index, total)

    def factory(self):
        script = self

        class Picker:
            def __init__(self, *args, context=None, initial_episode=None, **kwargs):
                self.context = context
                name = context.file.path.name
                script.seen.append((name, context.index, context.total))
                self.answer = script.answers[name]
                self.selected_season, self.selected_episode = None, None
                self.choice = CHOICE_SKIP
                self.stop_requested = False
                if self.answer == "episode":
                    number = int(name.split("E")[1][:2])
                    self.selected_season = 1
                    self.selected_episode = type("E", (), {"episode_number": number})()
                    self.choice = CHOICE_EPISODE
                elif self.answer == "show":
                    self.choice = CHOICE_SHOW_ONLY
                elif self.answer == "stop":
                    self.stop_requested = True

            def exec(self):
                return self.answer in ("episode", "show")

        return Picker


NAMES = ["Show.S01E01.mp4", "Show.S01E02.mp4", "Show.S01E03.mp4", "Show.S01E04.mp4"]


def setup_tmdb(monkeypatch, script, posters=None):
    monkeypatch.setattr(mw, "TMDBSearchDialog", ShowDialog)
    monkeypatch.setattr(mw, "get_tv_show_details", lambda i: dict(SHOW))
    monkeypatch.setattr(mw, "download_poster", lambda p: (posters.append(p) if posters is not None else None) or solid(600, 900))
    monkeypatch.setattr(mw, "get_tv_episode_details",
                        lambda i, s, e: {"title": f"Episode {e}", "season_number": s, "episode_number": e,
                                         "description": f"Plot {e}"})
    monkeypatch.setattr(mw, "TVEpisodePickerDialog", script.factory())


# -- TMDB TV: the owner's case ------------------------------------------------------------------------------------------

def test_a_skipped_file_gets_nothing_while_the_others_are_filled(monkeypatch, tmp_path):
    w = make_window(monkeypatch, tmp_path, NAMES)
    before = [state(vf) for vf in w.video_files]
    setup_tmdb(monkeypatch, Script({NAMES[0]: "episode", NAMES[1]: "episode", NAMES[2]: "skip", NAMES[3]: "episode"}))
    w._import_tmdb_tv(w.video_files, 0)
    skipped = w.video_files[2]
    assert state(skipped) == before[2]  # metadata, ids, source link, poster state and dirty flag all as before
    assert skipped.metadata.cover_pending is None and skipped.metadata.tmdb_id == "" and not skipped.dirty
    for index in (0, 1, 3):
        md = w.video_files[index].metadata
        assert md.content_type is ContentType.TV and md.show_title == "Breaking Bad" and md.network == "AMC"
        assert md.tmdb_id == f"episode:1396/1/{index + 1}" and md.cover_pending is not None
        assert md.title == f"Episode {index + 1}" and w.video_files[index].dirty
    assert "3 episode(s) matched" in w.status_bar.currentMessage()
    assert "0 show information only" in w.status_bar.currentMessage() and "1 skipped" in w.status_bar.currentMessage()
    w.undo_last_action()
    assert [state(vf) for vf in w.video_files] == before  # undo restores everything, skipped file exactly
    close(w)


def test_show_information_only_gives_the_show_but_no_episode_data(monkeypatch, tmp_path):
    w = make_window(monkeypatch, tmp_path, NAMES[:2])
    setup_tmdb(monkeypatch, Script({NAMES[0]: "episode", NAMES[1]: "show"}))
    w._import_tmdb_tv(w.video_files, 0)
    extra = w.video_files[1].metadata
    assert extra.show_title == "Breaking Bad" and extra.network == "AMC" and extra.cover_pending is not None
    assert extra.season_number is None and extra.episode_number is None and extra.title == ""
    assert extra.tmdb_id == "tv:1396" and extra.tvdb_id == "series:81189" and extra.imdb_id == "tt0903747"
    assert w.video_files[0].metadata.tmdb_id == "episode:1396/1/1"
    assert "1 episode(s) matched" in w.status_bar.currentMessage() and "1 show information only" in w.status_bar.currentMessage()
    close(w)


def test_escape_and_the_window_close_button_cancel_the_lookup_never_show_only(monkeypatch, tmp_path):
    class Source(EpisodeSource):
        def seasons(self, owner):
            return [(1, "Season 1")]

        def episodes(self, owner, season):
            return [type("E", (), {"episode_number": 1, "name": "Pilot", "overview": ""})()]

    escaped = []

    def esc(self):  # what Esc does to a QDialog
        self.reject()
        escaped.append(self.choice)
        return QDialog.DialogCode.Rejected

    class Real(EpisodePickerDialog):
        def __init__(self, tmdb_id, initial_season=None, initial_episode=None, parent=None, context=None):
            super().__init__(Source(), parent, initial_season, initial_episode, context=context)

    monkeypatch.setattr(Real, "exec", esc)
    w = make_window(monkeypatch, tmp_path, NAMES[:2])
    before = [state(vf) for vf in w.video_files]
    setup_tmdb(monkeypatch, Script({}))
    monkeypatch.setattr(mw, "TVEpisodePickerDialog", Real)
    w._import_tmdb_tv(w.video_files, 0)
    assert escaped == [CHOICE_SKIP]  # Esc in a batch cancels the whole lookup: the second file is never asked
    assert [state(vf) for vf in w.video_files] == before
    assert "0 episode(s) matched" in w.status_bar.currentMessage()
    close(w)


def test_stop_here_leaves_this_and_every_later_file_untouched(monkeypatch, tmp_path):
    w = make_window(monkeypatch, tmp_path, NAMES)
    before = [state(vf) for vf in w.video_files]
    script = Script({NAMES[0]: "episode", NAMES[1]: "stop", NAMES[2]: "episode", NAMES[3]: "episode"})
    setup_tmdb(monkeypatch, script)
    w._import_tmdb_tv(w.video_files, 0)
    assert [name for name, _i, _t in script.seen] == NAMES[:2]  # the queue ended: files 3 and 4 were never asked
    assert w.video_files[0].dirty and all(state(vf) == b for vf, b in zip(w.video_files[1:], before[1:]))
    assert "stopped, 3 file(s) not asked" in w.status_bar.currentMessage()
    close(w)


def test_the_picker_header_names_the_file_x_of_n(monkeypatch, tmp_path):
    w = make_window(monkeypatch, tmp_path, NAMES[:2])
    script = Script({NAMES[0]: "skip", NAMES[1]: "skip"})
    setup_tmdb(monkeypatch, script)
    w._import_tmdb_tv(w.video_files, 0)
    assert script.seen == [(NAMES[0], 1, 2), (NAMES[1], 2, 2)]
    close(w)


# -- the same scenario in the other flows -------------------------------------------------------------------------------

def test_tmdb_movies_cancel_applies_nothing(monkeypatch, tmp_path):
    w = make_window(monkeypatch, tmp_path, ["A.2001.mp4", "B.2002.mp4"])
    before = [state(vf) for vf in w.video_files]
    seen = []

    class Dlg(ShowDialog):
        def __init__(self, *a, context=None, **k):
            super().__init__(*a, **k)
            seen.append((context.file.path.name, context.index, context.total))
            self.selected_candidate = None  # cancelled

        def exec(self):
            return False

    monkeypatch.setattr(mw, "TMDBSearchDialog", Dlg)
    w._import_tmdb_movies(w.video_files, 0)
    assert seen == [("A.2001.mp4", 1, 2), ("B.2002.mp4", 2, 2)]
    assert [state(vf) for vf in w.video_files] == before
    close(w)


def test_tmdb_movies_stop_here_ends_the_queue(monkeypatch, tmp_path):
    w = make_window(monkeypatch, tmp_path, ["A.2001.mp4", "B.2002.mp4", "C.2003.mp4"])
    before = [state(vf) for vf in w.video_files]
    asked = []

    class Dlg(ShowDialog):
        def __init__(self, *a, context=None, **k):
            super().__init__(*a, **k)
            asked.append(context.file.path.name)
            self.selected_candidate, self.stop_requested = None, True

        def exec(self):
            return False

    monkeypatch.setattr(mw, "TMDBSearchDialog", Dlg)
    w._import_tmdb_movies(w.video_files, 0)
    assert asked == ["A.2001.mp4"] and [state(vf) for vf in w.video_files] == before
    assert "stopped: 3 file(s) not asked" in w.status_bar.currentMessage()
    close(w)


def test_tvdb_skip_leaves_the_file_untouched_and_show_only_gives_no_episode_data(monkeypatch, tmp_path):
    w = make_window(monkeypatch, tmp_path, [NAMES[0]])
    w.table.selectRow(0)
    vf = w.video_files[0]
    before = state(vf)
    monkeypatch.setattr(mw, "TVDBSearchDialog", ShowDialog)
    ShowDialog.candidate = type("C", (), {"tvdb_id": 73244, "name": "The Office"})()
    monkeypatch.setattr(mw, "get_series_details", lambda i: {"show_title": "The Office", "_poster_path": "https://x/p.jpg"})
    monkeypatch.setattr(mw, "download_image", lambda url: solid(680, 1000))
    answers = {"v": "skip"}

    class Picker:
        def __init__(self, *a, context=None, **k):
            self.stop_requested = False
            self.choice = CHOICE_SKIP if answers["v"] == "skip" else CHOICE_SHOW_ONLY
            self.selected_episode = None

        def exec(self):
            return answers["v"] != "skip"

    monkeypatch.setattr(mw, "TVDBEpisodePickerDialog", Picker)
    w._on_import_tvdb()
    assert state(vf) == before and not vf.dirty
    answers["v"] = "show"
    w._on_import_tvdb()
    md = vf.metadata
    assert md.show_title == "The Office" and md.tvdb_id == "series:73244" and md.cover_pending is not None
    assert md.episode_number is None and vf.dirty
    ShowDialog.candidate = tmdb_client.TVCandidate(1396, "Breaking Bad", "2008-01-20", "", "/p.jpg")
    close(w)


def test_scene_lookup_cancel_leaves_that_file_untouched(monkeypatch, tmp_path):
    from gui.scene_lookup_flow import run_scene_lookup
    from gui.scene_search_dialog import SceneSearchDialog
    from tests.test_scene_search_dialog import make_source, scene

    w = make_window(monkeypatch, tmp_path, ["One.mp4", "Two.mp4", "Three.mp4"])
    for vf in w.video_files:
        vf.path.write_bytes(os.urandom(200 * 1024))
    before = [state(vf) for vf in w.video_files]
    names = []

    def fake_exec(self):
        names.append((self.video.path.name, self.file_header.context.index, self.file_header.context.total))
        if self.video.path.name == "Two.mp4":
            return QDialog.DialogCode.Rejected  # cancelled: skipped
        self.table.selectRow(0)
        self._on_accept()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(SceneSearchDialog, "exec", fake_exec)
    from redactor_common.gui.overwrite_review_dialog import OverwriteReviewDialog
    monkeypatch.setattr(OverwriteReviewDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    run_scene_lookup(w, "theporndb", source_factory=lambda: make_source(by_title=[scene()]))
    assert names == [("One.mp4", 1, 3), ("Two.mp4", 2, 3), ("Three.mp4", 3, 3)]
    assert state(w.video_files[1]) == before[1]
    assert w.video_files[0].metadata.title == "Test Scene Title" and w.video_files[2].metadata.title == "Test Scene Title"
    close(w)


# -- the header ---------------------------------------------------------------------------------------------------------

def test_the_header_shows_file_x_of_n_the_full_name_the_guess_and_the_ids_it_has(tmp_path):
    long_name = "The.Office.US.S02E04.Halloween." + "Very.Long.Release.Group.Tag." * 3 + "1080p.mkv"
    path = tmp_path / long_name
    path.write_bytes(b"x" * 2048)
    vf = VideoFile(path=path, metadata=VideoMetadata(tmdb_id="movie:603", resolution="1920x1080", duration_seconds=1334))
    header = LookupFileHeader(file_context(vf, 3, 12))
    header.resize(420, 120)
    header.show()
    QApplication.processEvents()
    assert header.counter_label.text() == "File 3 of 12" and not header.counter_label.isHidden()
    assert header.name_label.full_text() == long_name and header.name_label.toolTip() == str(path)
    assert "…" in header.name_label.text() and len(header.name_label.text()) < len(long_name)  # cut in the middle
    assert str(tmp_path) in header.details_label.text() and "1920x1080" in header.details_label.text()
    assert "22:14" in header.details_label.text() and "2.0 KB" in header.details_label.text()
    assert header.searching_label.text() == "Searching for: The Office US S02E04"
    assert header.existing_label.text() == "This file already has TMDB movie:603"
    assert not header.skip_button.isHidden() and not hasattr(header, "stop_button")
    single = LookupFileHeader(file_context(vf))
    single.show()
    assert single.counter_label.isHidden() and single.skip_button.isHidden()
    header.close()
    single.close()


def test_the_guess_text():
    assert searching_for_text("Inception.2010.1080p.BluRay") == "Inception (2010)"
    assert searching_for_text("Show.S02E04.720p") == "Show S02E04"


def test_skip_and_cancel_close_the_dialog_without_choosing(monkeypatch, tmp_path):
    from gui.tmdb_search_dialog import TMDBSearchDialog

    path = tmp_path / "Inception.2010.mp4"
    path.write_bytes(b"x")
    vf = VideoFile(path=path, metadata=VideoMetadata())
    dialog = TMDBSearchDialog("movie", initial_query="Inception", context=file_context(vf, 2, 5))
    assert dialog.file_header is not None and dialog.stop_requested is False
    dialog.file_header.skip_button.click()  # Skip: this file only
    assert dialog.stop_requested is False and dialog.result() == QDialog.DialogCode.Rejected
    dialog = TMDBSearchDialog("movie", initial_query="Inception", context=file_context(vf, 2, 5))
    dialog.cancel_button.click()  # Cancel: the whole lookup
    assert dialog.stop_requested is True and dialog.result() == QDialog.DialogCode.Rejected
    plain = TMDBSearchDialog("movie", initial_query="Inception")  # no context: no header, still has the attribute
    assert plain.file_header is None and plain.stop_requested is False


def test_the_real_picker_has_three_choices_plus_cancel_lookup(tmp_path):
    class Source(EpisodeSource):
        def seasons(self, owner):
            return [(1, "Season 1")]

        def episodes(self, owner, season):
            return [type("E", (), {"episode_number": 1, "name": "Pilot", "overview": "p"})()]

    path = tmp_path / "Show.S01E01.mp4"
    path.write_bytes(b"x")
    vf = VideoFile(path=path, metadata=VideoMetadata())
    d = EpisodePickerDialog(Source(), context=file_context(vf, 1, 3))
    assert [d.select_button.text(), d.show_only_button.text(), d.skip_button.text(), d.cancel_button.text()] == [
        "Use This Episode", "Show Information Only", "Skip This File", "Cancel Lookup"]
    assert not d.select_button.isEnabled() and d.show_only_button.isEnabled()
    d.episode_list.setCurrentRow(0)
    d.select_button.click()
    assert d.choice == CHOICE_EPISODE and d.selected_episode is not None
    d2 = EpisodePickerDialog(Source())
    d2.show_only_button.click()
    assert d2.choice == CHOICE_SHOW_ONLY and d2.selected_episode is None and d2.result() == QDialog.DialogCode.Accepted
    d3 = EpisodePickerDialog(Source())
    d3.reject()  # Esc / X
    assert d3.choice == CHOICE_SKIP


# -- real MP4s: Save leaves the skipped file byte for byte as it was --------------------------------------------------

@pytest.mark.skipif(bool(SKIP_REASON), reason=SKIP_REASON or "sample clip")
def test_saving_after_a_skip_leaves_the_skipped_file_byte_for_byte_and_writes_the_rest(monkeypatch, tmp_path):
    w = make_window(monkeypatch, tmp_path, NAMES, real=True)
    skipped_path = w.video_files[2].path
    digest_before = digest(skipped_path)
    from mutagen.mp4 import MP4
    tags_before = dict(MP4(str(skipped_path)).tags or {})
    setup_tmdb(monkeypatch, Script({NAMES[0]: "episode", NAMES[1]: "episode", NAMES[2]: "skip", NAMES[3]: "show"}))
    w._import_tmdb_tv(w.video_files, 0)
    w._on_save_all()
    assert digest(skipped_path) == digest_before
    assert dict(MP4(str(skipped_path)).tags or {}) == tags_before
    for index, expect_title in ((0, "Episode 1"), (1, "Episode 2"), (3, "")):
        tags = MP4(str(w.video_files[index].path)).tags
        assert tags["tvsh"] == ["Breaking Bad"] and "covr" in tags
        assert (tags.get("\xa9nam") or [""])[0] == expect_title
    assert "tves" not in MP4(str(w.video_files[3].path)).tags  # show information only: no episode number
    close(w)
