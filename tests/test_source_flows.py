"""Source ids and the embedded poster in the lookups: the TMDB / TheTVDB / IMDb flows on the real MainWindow (offscreen,
scratch settings, FAKE clients and dialogs), the TMDB dialog's 'File has an ID' row, the Redact steps that use an id
already in the file, the side panel's Sources section, the Sources column and the command line. No authenticated live
call is possible here (no keys): every service is a fake."""

import os
import shutil

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402
from redactor_common.core.pipeline import FileStatus  # noqa: E402

import gui.main_window as mw  # noqa: E402
from core import redact_steps as rs  # noqa: E402
from core import stashdb_client, theporndb_client, tmdb_client, tvdb_client  # noqa: E402
from core.scene_lookup import MATCH_FILE, MATCH_ID, SceneResult  # noqa: E402
from core.video_file import VideoFile  # noqa: E402
from core.video_metadata import ContentType, VideoMetadata  # noqa: E402
from gui.tmdb_search_dialog import TMDBSearchDialog  # noqa: E402
from tests.test_poster import solid  # noqa: E402
from tests.test_redact_steps import bin_, fake_tmdb, lookup_entry, make_env, movie, only, show, stub, work  # noqa: E402,F401

_app = QApplication.instance() or QApplication([])
U = "d284e2a0-789d-4e6c-beb9-6e61c24e8b84"


# --- the window -------------------------------------------------------------------------------------------------------


@pytest.fixture
def window(monkeypatch, tmp_path):
    import core.config as config

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")
    monkeypatch.setattr(mw.MainWindow, "_restore_last_folder_on_startup", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_check_external_tools_on_startup", lambda self: None)
    w = mw.MainWindow()
    path = tmp_path / "Inception.2010.mp4"
    path.write_bytes(b"x" * 1000)
    w.video_files = [VideoFile(path=path, metadata=VideoMetadata())]
    w._refresh_table_rows()
    w.table.selectRow(0)
    yield w
    for vf in w.video_files:
        vf.dirty = False
    w.close()


class FakeDialog:
    """Stands in for TMDBSearchDialog / TVDBSearchDialog: records its arguments, picks `candidate`."""

    instances: list = []
    candidate = None
    save_poster = True
    accept_landscape = False
    selected_how = "title"

    def __init__(self, *args, **kwargs):
        self.args, self.kwargs = args, kwargs
        self.selected_candidate = type(self).candidate
        self.save_poster = type(self).save_poster
        self.accept_landscape = type(self).accept_landscape
        self.selected_how = type(self).selected_how
        type(self).instances.append(self)

    def exec(self):
        return QDialog.DialogCode.Accepted


@pytest.fixture
def fake_dialog(monkeypatch):
    FakeDialog.instances = []
    FakeDialog.save_poster, FakeDialog.accept_landscape, FakeDialog.selected_how = True, False, "title"
    FakeDialog.candidate = tmdb_client.MovieCandidate(603, "The Matrix", "1999-03-31", "", "/p.jpg")
    monkeypatch.setattr(mw, "TMDBSearchDialog", FakeDialog)
    monkeypatch.setattr(mw, "TVDBSearchDialog", FakeDialog)
    return FakeDialog


@pytest.fixture
def tmdb(monkeypatch):
    calls = {"details": [], "poster": []}

    def details(i):
        calls["details"].append(i)
        return {"title": "The Matrix", "description": "Plot.", "genre_tags": "Sci-Fi", "release_date": "1999-03-31",
                "director": "Wachowski", "_poster_path": "/p.jpg", "_imdb_id": "tt0133093"}

    def poster(path):
        calls["poster"].append(path)
        return solid(600, 900)

    monkeypatch.setattr(mw, "get_movie_details", details)
    monkeypatch.setattr(mw, "download_poster", poster)
    return calls


def listing(folder):
    return sorted(os.listdir(folder))


@pytest.fixture(autouse=True)
def boxes(monkeypatch):
    """Message boxes are recorded, never shown (a modal box would hang the run)."""
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text, *a, **k: shown.append(("warning", title, text)))
    monkeypatch.setattr(QMessageBox, "information", lambda parent, title, text, *a, **k: shown.append(("info", title, text)))
    return shown


def review_dialog(monkeypatch, accept=True, shown=None, tick=()):
    """Replaces the review dialog's exec: records it, ticks the rows named in `tick` (attribute names), answers."""
    from redactor_common.gui.overwrite_review_dialog import OverwriteReviewDialog

    def fake_exec(self):
        if shown is not None:
            shown.append(self)
        for (index, attr), box in self._controller._checkboxes.items():
            if attr in tick:
                box.setChecked(True)
        return QDialog.DialogCode.Accepted if accept else QDialog.DialogCode.Rejected

    monkeypatch.setattr(OverwriteReviewDialog, "exec", fake_exec)


def test_tmdb_movie_stages_the_ids_and_the_poster_and_creates_no_file(window, fake_dialog, tmdb, tmp_path):
    before = listing(tmp_path)
    vf = window.video_files[0]
    window._import_tmdb_movies([vf], 0)
    md = vf.metadata
    assert (md.tmdb_id, md.imdb_id) == ("movie:603", "tt0133093") and md.source_url == "https://www.themoviedb.org/movie/603"
    assert md.metadata_match.startswith("tmdb;") and md.metadata_match.endswith(";title")
    assert md.cover_pending[:3] == b"\xff\xd8\xff" and md.title == "The Matrix" and vf.dirty
    assert tmdb["poster"] == ["/p.jpg"] and "poster(s) will be embedded on Save" in window.status_bar.currentMessage()
    assert listing(tmp_path) == before  # no <name>-poster.jpg, no file at all
    assert not hasattr(md, "_imdb_id")  # the underscore keys never became attributes


def test_tmdb_movie_without_the_poster_box_downloads_nothing(window, fake_dialog, tmdb, tmp_path):
    fake_dialog.save_poster = False
    window._import_tmdb_movies(window.video_files, 0)
    assert tmdb["poster"] == [] and window.video_files[0].metadata.cover_pending is None
    assert window.video_files[0].metadata.tmdb_id == "movie:603"


def test_tmdb_movie_landscape_picture_is_not_embedded(window, fake_dialog, tmdb, monkeypatch, boxes):
    monkeypatch.setattr(mw, "download_poster", lambda path: solid(900, 600))
    window._import_tmdb_movies(window.video_files, 0)
    md = window.video_files[0].metadata
    assert md.cover_pending is None and md.tmdb_id == "movie:603"  # the ids are still applied, the poster is not
    assert any("No poster available from TMDB" in text and "not portrait" in text for _kind, _title, text in boxes)


def test_tmdb_movie_landscape_accepted_when_the_box_is_ticked(window, fake_dialog, tmdb, monkeypatch):
    monkeypatch.setattr(mw, "download_poster", lambda path: solid(900, 600))
    fake_dialog.accept_landscape = True
    window._import_tmdb_movies(window.video_files, 0)
    assert window.video_files[0].metadata.cover_pending is not None


def test_a_different_id_already_in_the_file_goes_through_the_review_and_is_kept_by_default(window, fake_dialog, tmdb, monkeypatch):
    vf = window.video_files[0]
    vf.metadata.tmdb_id = "movie:1"
    shown = []
    review_dialog(monkeypatch, accept=True, shown=shown)
    window._import_tmdb_movies([vf], 0)
    assert len(shown) == 1  # the review appeared because a DIFFERENT value is in the file
    assert vf.metadata.tmdb_id == "movie:1"  # the overwrite starts unticked: the old id stays
    assert vf.metadata.imdb_id == "tt0133093"  # a blank field is safe to fill and starts ticked


def test_the_same_id_again_needs_no_review(window, fake_dialog, tmdb, monkeypatch):
    vf = window.video_files[0]
    vf.metadata.tmdb_id = "movie:603"
    from redactor_common.gui import overwrite_review_dialog as ord_

    monkeypatch.setattr(ord_.OverwriteReviewDialog, "exec", lambda self: pytest.fail("review shown for an identical id"))
    window._import_tmdb_movies([vf], 0)
    assert vf.metadata.tmdb_id == "movie:603" and vf.metadata.cover_pending is not None


def test_an_existing_cover_is_replaced_only_when_the_review_ticks_it(window, fake_dialog, tmdb, monkeypatch):
    vf = window.video_files[0]
    vf.metadata.cover_info = "JPEG 600x900, 90 KB"
    shown = []
    review_dialog(monkeypatch, accept=True, shown=shown)
    window._import_tmdb_movies([vf], 0)
    assert len(shown) == 1 and vf.metadata.cover_pending is None  # unticked by default: the old cover stays
    table = shown[0]._controller.table
    labels = [table.item(r, 1).text() for r in range(table.rowCount())]
    assert "Poster (embedded)" in labels
    row = labels.index("Poster (embedded)")
    assert table.item(row, 2).text() == "JPEG 600x900, 90 KB" and table.item(row, 3).text().startswith("new poster: JPEG 600x900")

    review_dialog(monkeypatch, accept=True, tick=("cover_info",))  # the user ticks the replacement
    window._import_tmdb_movies([vf], 0)
    assert vf.metadata.cover_pending is not None


def test_a_cancelled_review_stages_no_id_and_no_poster(window, fake_dialog, tmdb, monkeypatch):
    vf = window.video_files[0]
    vf.metadata.tmdb_id = "movie:1"
    review_dialog(monkeypatch, accept=False)
    window._import_tmdb_movies([vf], 0)
    assert vf.metadata.tmdb_id == "movie:1" and vf.metadata.cover_pending is None and vf.metadata.imdb_id == ""


def test_an_id_replacement_needs_the_tick(window, fake_dialog, tmdb, monkeypatch):
    vf = window.video_files[0]
    vf.metadata.tmdb_id = "movie:1"
    review_dialog(monkeypatch, accept=True, tick=("tmdb_id",))
    window._import_tmdb_movies([vf], 0)
    assert vf.metadata.tmdb_id == "movie:603"


def test_the_file_s_own_tmdb_id_is_offered_first(window, fake_dialog, tmdb):
    vf = window.video_files[0]
    vf.metadata.tmdb_id = "movie:603"
    window._import_tmdb_movies([vf], 0)
    existing = fake_dialog.instances[0].kwargs["existing"]
    candidate, text = existing
    assert candidate.tmdb_id == 603 and "603" in text
    vf.metadata.tmdb_id = ""
    fake_dialog.instances.clear()
    window._import_tmdb_movies([vf], 0)
    assert "existing" not in fake_dialog.instances[0].kwargs  # no id in the file: the plain search dialog


def test_tmdb_tv_uses_the_episode_id_and_one_poster_for_every_file(window, fake_dialog, monkeypatch, tmp_path):
    second = tmp_path / "Show.S01E03.mp4"
    second.write_bytes(b"x" * 1000)
    files = [VideoFile(path=tmp_path / "Show.S01E02.mp4", metadata=VideoMetadata()), VideoFile(path=second, metadata=VideoMetadata())]
    for f in files:
        f.path.write_bytes(b"x" * 1000)
    window.video_files = files
    window._refresh_table_rows()
    fake_dialog.candidate = tmdb_client.TVCandidate(1396, "Breaking Bad", "2008-01-20", "", "/p.jpg")
    posters = []
    monkeypatch.setattr(mw, "get_tv_show_details", lambda i: {
        "show_title": "Breaking Bad", "description": "Show.", "release_date": "2008-01-20", "network": "AMC",
        "_poster_path": "/p.jpg", "_imdb_id": "tt0903747", "_tvdb_id": 81189})
    monkeypatch.setattr(mw, "download_poster", lambda p: posters.append(p) or solid(600, 900))
    monkeypatch.setattr(mw, "get_tv_episode_details", lambda i, s, e: {"title": f"Ep {e}", "season_number": s, "episode_number": e})

    class Picker:
        n = 0

        def __init__(self, *a, **k):
            Picker.n += 1
            self.selected_season = 1
            self.selected_episode = type("E", (), {"episode_number": 1 + Picker.n})()

        def exec(self):
            return True

    monkeypatch.setattr(mw, "TVEpisodePickerDialog", Picker)
    window._import_tmdb_tv(files, 0)
    assert [f.metadata.tmdb_id for f in files] == ["episode:1396/1/2", "episode:1396/1/3"]
    assert all(f.metadata.tvdb_id == "series:81189" and f.metadata.imdb_id == "tt0903747" for f in files)
    assert posters == ["/p.jpg"]  # one download for the whole batch
    assert files[0].metadata.cover_pending is not None and files[0].metadata.cover_pending == files[1].metadata.cover_pending


def test_tvdb_stages_the_series_id_and_its_poster(window, fake_dialog, monkeypatch):
    fake_dialog.candidate = type("C", (), {"tvdb_id": 73244, "name": "The Office"})()
    monkeypatch.setattr(mw, "get_series_details", lambda i: {"show_title": "The Office", "_poster_path": "https://art.example.com/p.jpg"})
    monkeypatch.setattr(mw.MainWindow, "_apply_tvdb_episode_details", lambda self, *a, **k: None)
    monkeypatch.setattr(mw, "download_image", lambda url: solid(680, 1000))
    window._on_import_tvdb()
    md = window.video_files[0].metadata
    assert md.tvdb_id == "series:73244" and md.source_url == "https://thetvdb.com/dereferrer/series/73244"
    assert md.cover_pending is not None and md.show_title == "The Office"


def test_tvdb_without_the_box_ticked_embeds_nothing(window, fake_dialog, monkeypatch):
    fake_dialog.save_poster = False
    fake_dialog.candidate = type("C", (), {"tvdb_id": 73244, "name": "The Office"})()
    monkeypatch.setattr(mw, "get_series_details", lambda i: {"show_title": "The Office", "_poster_path": "https://art.example.com/p.jpg"})
    monkeypatch.setattr(mw.MainWindow, "_apply_tvdb_episode_details", lambda self, *a, **k: None)
    monkeypatch.setattr(mw, "download_image", lambda url: pytest.fail("downloaded without the box"))
    window._on_import_tvdb()
    assert window.video_files[0].metadata.cover_pending is None and window.video_files[0].metadata.tvdb_id == "series:73244"


# --- the real TMDB dialog ----------------------------------------------------------------------------------------------


def test_the_dialog_lists_the_file_s_id_first_and_does_not_search(monkeypatch):
    monkeypatch.setattr("gui.tmdb_search_dialog.search_movies", lambda *a, **k: pytest.fail("searched"))
    candidate = tmdb_client.MovieCandidate(603, "TMDB movie 603", "", "From the file.", None)
    dialog = TMDBSearchDialog("movie", initial_query="The Matrix", existing=(candidate, "TMDB movie 603"))
    assert dialog.results_list.count() == 1 and dialog.results_list.item(0).text() == "File has an ID: TMDB movie 603"
    assert dialog.results_list.currentRow() == 0 and dialog.select_button.isEnabled()
    dialog._on_accept()
    assert dialog.selected_candidate is candidate and dialog.selected_how == "manual"


def test_a_search_keeps_the_id_row_on_top(monkeypatch):
    found = [tmdb_client.MovieCandidate(1, "Other", "2000-01-01", "", None)]
    monkeypatch.setattr("gui.tmdb_search_dialog.search_movies", lambda q, year=None: found)
    monkeypatch.setattr("gui.tmdb_search_dialog.run_lookup", lambda owner, fn, *a, **k: fn(*a, **k))
    candidate = tmdb_client.MovieCandidate(603, "TMDB movie 603", "", "", None)
    dialog = TMDBSearchDialog("movie", initial_query="x", existing=(candidate, "TMDB movie 603"))
    dialog._on_search()
    assert [dialog.results_list.item(i).text().split(" (")[0] for i in range(2)] == ["File has an ID: TMDB movie 603", "Other"]
    dialog.results_list.setCurrentRow(1)
    dialog._on_accept()
    assert dialog.selected_how == "title"


def test_the_dialog_has_the_renamed_poster_box_on_by_default_and_a_landscape_box_off(window):
    dialog = TMDBSearchDialog("movie")
    assert dialog.poster_box.text() == "Save the poster in the file" and dialog.poster_box.isChecked()
    assert dialog.landscape_box.text() == "Accept a landscape cover when there is no poster" and not dialog.landscape_box.isChecked()
    dialog.poster_box.setChecked(False)
    dialog.results_list.addItem("x")
    candidate = tmdb_client.MovieCandidate(1, "A", "", "", None)
    dialog.results_list.item(0).setData(Qt.ItemDataRole.UserRole, candidate)
    dialog.results_list.setCurrentRow(0)
    dialog._on_accept()
    assert dialog.save_poster is False
    assert TMDBSearchDialog("movie").poster_box.isChecked() is False  # the choice is remembered (the old default was ON)


def test_the_imdb_dialog_has_no_poster_box():
    from gui.tmdb_search_dialog import SearchSource

    dialog = TMDBSearchDialog("movie", source=SearchSource(name="IMDb", movies=lambda q, year=None: [], tv=lambda q, year=None: []))
    assert dialog.poster_box is None and dialog.landscape_box is None  # the datasets have no posters


# --- Redact steps use the id already in the file ------------------------------------------------------------------------


def apply_lookup(vf, env):
    ctx = rs.VideoCtx(vf, env)
    step = rs.LookupStep()
    result = step.run(ctx)
    return ctx, step, result


def test_redact_lookup_writes_the_ids_of_what_it_applies(work, bin_, tmp_path, monkeypatch):
    fake_tmdb(monkeypatch, movies=[movie("Inception", 2010, 27205)])
    vf = stub(work, "Inception.2010.1080p.mp4")
    ctx, step, result = apply_lookup(vf, make_env(bin_, tmp_path))
    lines = step.apply_suggestion(ctx, result)
    md = ctx.work.metadata
    assert md.tmdb_id == "movie:27205" and md.source_url == "https://www.themoviedb.org/movie/27205"
    assert md.metadata_match.endswith(";title") and any("tmdb_id" in line for line in lines)
    assert vf.metadata.tmdb_id == ""  # the live row is untouched until the commit


def test_redact_lookup_uses_the_tmdb_id_in_the_file_and_does_not_search(work, bin_, tmp_path, monkeypatch):
    fake_tmdb(monkeypatch, movies=[])
    monkeypatch.setattr(tmdb_client, "search_movies", lambda *a, **k: pytest.fail("searched although the file has an id"))
    asked = []
    original = tmdb_client.get_movie_details
    monkeypatch.setattr(tmdb_client, "get_movie_details", lambda i: asked.append(i) or original(i))
    vf = stub(work, "Inception.2010.1080p.mp4", tmdb_id="movie:27205")
    ctx, step, result = apply_lookup(vf, make_env(bin_, tmp_path))
    assert asked == [27205] and result.confidence >= 0.97 and "TMDB id in the file" in result.reason
    step.apply_suggestion(ctx, result)
    assert ctx.work.metadata.tmdb_id == "movie:27205" and ctx.work.metadata.director == "Nolan"
    assert ctx.work.metadata.metadata_match.endswith(";manual")  # the id came from the file, nobody searched


def test_redact_never_replaces_an_id_that_is_in_the_file(work, bin_, tmp_path, monkeypatch):
    fake_tmdb(monkeypatch, movies=[movie("Inception", 2010, 99)])
    vf = stub(work, "Inception.2010.1080p.mp4", imdb_id="tt0000001", source_url="https://www.imdb.com/title/tt0000001/")
    ctx = rs.VideoCtx(vf, make_env(bin_, tmp_path))
    fill = rs.LookupFill("TMDB", "x", {"title": "x"}, ContentType.MOVIE, "2010",
                         {"imdb_id": "tt0999999", "source_url": "https://www.imdb.com/title/tt0999999/", "tmdb_id": "movie:99"})
    rs.LookupStep().apply_suggestion(ctx, rs.StepResult.suggestion(fill, 0.95, "x"))
    md = ctx.work.metadata
    assert md.imdb_id == "tt0000001" and md.source_url == "https://www.imdb.com/title/tt0000001/"  # kept
    assert md.tmdb_id == "movie:99"  # the empty one was filled


def test_redact_tv_uses_the_tvdb_id_when_that_is_all_the_file_has(work, bin_, tmp_path, monkeypatch):
    fake_tmdb(monkeypatch, shows=[])
    monkeypatch.setattr(tmdb_client, "search_tv", lambda *a: pytest.fail("TMDB searched"))
    monkeypatch.setattr(tvdb_client, "search_series", lambda *a: pytest.fail("TheTVDB searched"))
    monkeypatch.setattr(tvdb_client, "get_series_details", lambda i: {
        "show_title": "The Office", "description": "Show.", "release_date": "2005-03-24", "network": "NBC", "_poster_path": None})
    monkeypatch.setattr(tvdb_client, "get_episode_details", lambda i, s, e: {
        "title": "Halloween", "season_number": s, "episode_number": e})
    vf = stub(work, "The Office S02E05.mp4", tvdb_id="series:73244")
    ctx, step, result = apply_lookup(vf, make_env(bin_, tmp_path))
    assert result.status.name == "SUGGESTION" and "TheTVDB id in the file" in result.reason
    step.apply_suggestion(ctx, result)
    assert ctx.work.metadata.title == "Halloween" and ctx.work.metadata.tvdb_id == "series:73244"
    assert ctx.work.metadata.source_url.startswith("https://thetvdb.com/")


def test_redact_scene_step_asks_for_the_id_in_the_file_not_the_hash(work, bin_, tmp_path, monkeypatch):
    monkeypatch.setenv("THEPORNDB_API_KEY", "k" * 40)
    monkeypatch.setenv("STASHDB_API_KEY", "k" * 40)
    monkeypatch.setattr(theporndb_client, "search_by_hash", lambda *a: pytest.fail("hash search although the file has an id"))
    monkeypatch.setattr(stashdb_client, "search_by_hash", lambda *a: [])
    asked = []
    monkeypatch.setattr(theporndb_client, "get", lambda ident, kind="scene": asked.append((ident, kind)) or SceneResult(
        source="ThePornDB", external_id=U, title="Synthetic Scene", studio="Example Studio", match_kind="title match"))
    path = work / "clip.mp4"
    path.write_bytes(os.urandom(200 * 1024))
    vf = VideoFile(path=path, metadata=VideoMetadata(theporndb_id=U))
    entry = run_one_review(vf, bin_, tmp_path)
    assert asked == [(U, "scene")]
    assert entry.status is FileStatus.NEEDS_REVIEW and "id in the file" in entry.review[0].reason


def run_one_review(vf, bin_, tmp_path):
    from tests.test_redact_steps import run_one

    recipe, catalogue = only("scene_lookup", threshold=0.5)
    return run_one(vf, make_env(bin_, tmp_path), recipe, catalogue)


def test_a_scene_match_by_hash_carries_the_stashdb_id_when_applied(work, bin_, tmp_path):
    path = work / "clip.mp4"
    path.write_bytes(b"x" * 10)
    vf = VideoFile(path=path, metadata=VideoMetadata())
    ctx = rs.VideoCtx(vf, rs.RedactEnv())
    fill = rs.LookupFill("StashDB", "Scene", {"title": "Scene"}, ContentType.MOVIE, "2021",
                         {"stashdb_id": U, "source_url": "https://stashdb.org/scenes/" + U, "metadata_match": "stashdb;2026-10-16;file"})
    lines = rs.SceneLookupStep().apply_suggestion(ctx, rs.StepResult.suggestion(fill, 0.95, "x", review_only=True))
    assert ctx.work.metadata.stashdb_id == U and ctx.work.metadata.metadata_match == "stashdb;2026-10-16;file"
    assert any("stashdb_id" in line for line in lines)


def test_a_redact_save_writes_ids_even_when_only_ids_changed(work, bin_, tmp_path):
    vf = stub(work, "a.mp4")
    ctx = rs.VideoCtx(vf, make_env(bin_, tmp_path))
    assert not ctx.needs_write()
    ctx.work.metadata.tmdb_id = "movie:1"
    assert ctx.needs_write()  # compare=False fields are checked explicitly


# --- the side panel and the table ---------------------------------------------------------------------------------------


def test_the_sources_section_is_collapsed_read_only_and_links_are_built_from_validated_ids(window):
    vf = window.video_files[0]
    vf.metadata = VideoMetadata(tmdb_id="movie:603", imdb_id="tt0133093", source_url="https://www.themoviedb.org/movie/603",
                                metadata_match="tmdb;2026-10-16;title", stashdb_id="<script>alert(1)</script>")
    window._on_selection_changed()
    panel = window.tag_panel
    assert panel.sources_toggle.text().startswith("Sources") and not panel.sources_toggle.isChecked()
    assert panel.sources_label.isHidden()  # collapsed
    text = panel.sources_label.text()
    assert '<a href="https://www.themoviedb.org/movie/603">movie:603</a>' in text
    assert '<a href="https://www.imdb.com/title/tt0133093/">tt0133093</a>' in text
    assert "<script>" not in text and "&lt;script&gt;" in text  # shown escaped, never as a link
    assert "tmdb_id" not in panel._inputs and "source_url" not in panel._inputs  # not an editable field
    panel.sources_toggle.setChecked(True)
    assert not panel.sources_label.isHidden()


def test_the_sources_section_for_no_selection_and_for_several(window, tmp_path):
    window.table.clearSelection()
    window.tag_panel.set_sources([])
    assert window.tag_panel.sources_label.text() == ""
    window.tag_panel.set_sources(window.video_files * 2)
    assert "one file" in window.tag_panel.sources_label.text()
    window.tag_panel.set_sources(window.video_files)
    assert "no source ids" in window.tag_panel.sources_label.text()


def test_the_sources_column_is_optional_and_starts_hidden(window):
    assert "sources" in window._column_order
    column = window._column_order.index("sources")
    assert window.table.isColumnHidden(column)
    window.video_files[0].metadata.imdb_id = "tt0133093"
    window.video_files[0].metadata.tmdb_id = "movie:603"
    window._refresh_table_rows()
    assert window.table.item(0, column).text() == "TMDB movie:603, IMDb tt0133093"


def test_the_panel_caption_says_poster_or_frame_preview(window, monkeypatch):
    vf = window.video_files[0]
    vf.preview_kind = "Poster"
    from PyQt6.QtGui import QImage

    window._on_preview_ready(vf, QImage(10, 15, QImage.Format.Format_RGB32))
    assert window.tag_panel.preview_box.title() == "Poster"
    vf.preview_kind = "Frame preview"
    window._on_preview_ready(vf, QImage(16, 9, QImage.Format.Format_RGB32))
    assert window.tag_panel.preview_box.title() == "Frame preview"


# --- the command line -------------------------------------------------------------------------------------------------


from tests.sample_clip import SAMPLE, SKIP_REASON  # noqa: E402


@pytest.mark.skipif(bool(SKIP_REASON), reason=SKIP_REASON or "sample clip")
def test_cli_info_shows_the_read_only_source_fields_and_set_refuses_them(tmp_path, capsys, monkeypatch):
    import core.config as config
    from redactor_common.cli import CliError
    from videocli.main import main

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "videoredactor_settings.ini")
    clip = tmp_path / "Show.S01E01.mp4"
    shutil.copy(SAMPLE, clip)
    vf = VideoFile(path=clip)
    vf.load()
    vf.metadata.tmdb_id, vf.metadata.source_url = "tv:1396", "https://www.themoviedb.org/tv/1396"
    from core.poster import prepare_poster

    vf.metadata.cover_pending = prepare_poster(solid(600, 900)).jpeg
    vf.dirty = True
    vf.save()
    assert vf.save_error == ""
    assert main(["info", str(clip), "--fields", "tmdb_id,source_url,cover"]) == 0
    out = capsys.readouterr().out
    assert "tmdb_id: tv:1396" in out and "source_url: https://www.themoviedb.org/tv/1396" in out and "cover_info: JPEG 600x900" in out
    main(["info", str(clip), "--all"])
    assert "tmdb_id: tv:1396" in capsys.readouterr().out
    with pytest.raises(CliError, match="read-only"):
        main(["set", str(clip), "-s", "tmdb_id=movie:1"])
    with pytest.raises(CliError, match="read-only"):
        main(["set", str(clip), "--clear", "source_url"])
    assert main(["info", str(clip), "--fields", "tmdb_id"]) == 0
