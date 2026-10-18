"""Get Poster: a true poster from the source ids a file already carries (core/poster_fetch.py), the Metadata > Get Poster flow
(staged like a lookup: one Undo entry, Save embeds it) and the Redact step 'Get the poster from the file's source ids'.
The services are stood in for; the pictures are real images, so the true-poster rule (core/poster.py) really runs."""

from types import SimpleNamespace

import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox

import core.config as config
from core import api_keys, poster_fetch as pf
from core import redact_steps as rs
from core import stashdb_client, theporndb_client, tmdb_client, tvdb_client
from core.scene_lookup import SceneResult
from core.video_metadata import ContentType, VideoMetadata
from tests.test_poster import solid


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    """Every service has a key unless a test removes it."""
    monkeypatch.setattr(api_keys, "get_key", lambda name: "key")


POSTER = None


def poster_bytes():
    global POSTER
    if POSTER is None:
        POSTER = solid(600, 900, "PNG")
    return POSTER


def meta(**fields):
    return VideoMetadata(**fields)


# -- which services, in which order -----------------------------------------------------------------------------------------


def test_services_follow_the_ids_in_the_file_and_the_content_type():
    film = meta(content_type=ContentType.MOVIE, tmdb_id="movie:603", imdb_id="tt0133093", tvdb_id="series:5")
    assert pf.services_for(film) == ["tmdb", "tvdb"]  # an IMDb id is only a way in when there is no TMDB id
    assert pf.services_for(meta(imdb_id="tt0133093")) == ["imdb"]
    scene = meta(content_type=ContentType.PORN, stashdb_id="2f5a8b0e-0a4c-4b57-8d6b-1f1d0e6a9b11", theporndb_id="abc-123")
    assert pf.services_for(scene)[:2] == ["theporndb", "stashdb"]
    assert pf.services_for(meta()) == []


# -- fetching ---------------------------------------------------------------------------------------------------------------


def test_a_film_gets_the_poster_of_its_tmdb_record(monkeypatch):
    calls = []
    monkeypatch.setattr(tmdb_client, "get_movie_details", lambda i: calls.append(i) or {"_poster_path": "/p.jpg"})
    monkeypatch.setattr(tmdb_client, "download_poster", lambda path: poster_bytes())
    outcome = pf.fetch_poster(meta(tmdb_id="movie:603"))
    assert outcome.found and outcome.service == "TMDB" and calls == [603]
    assert outcome.poster.jpeg[:2] == b"\xff\xd8"  # prepared as a JPEG


def test_a_tv_episode_gets_the_poster_of_its_series(monkeypatch):
    calls = []
    monkeypatch.setattr(tmdb_client, "get_tv_show_details", lambda i: calls.append(i) or {"_poster_path": "/s.jpg"})
    monkeypatch.setattr(tmdb_client, "download_poster", lambda path: poster_bytes())
    assert pf.fetch_poster(meta(tmdb_id="episode:1396/1/2")).found and calls == [1396]
    assert pf.fetch_poster(meta(tmdb_id="season:1396/1")).found and calls == [1396, 1396]


def test_the_next_source_is_tried_when_the_first_fails_and_the_notes_say_why(monkeypatch):
    def broken(i):
        raise tmdb_client.TMDBError("TMDB rate limit hit")

    monkeypatch.setattr(tmdb_client, "get_movie_details", broken)
    monkeypatch.setattr(tvdb_client, "get_series_details", lambda i: {"_poster_path": "https://x/p.jpg"})
    monkeypatch.setattr(tvdb_client, "download_image", lambda url: poster_bytes())
    outcome = pf.fetch_poster(meta(tmdb_id="movie:1", tvdb_id="series:73244"))
    assert outcome.found and outcome.service == "TheTVDB" and any("rate limit" in n for n in outcome.notes)


def test_an_imdb_id_goes_through_tmdb(monkeypatch):
    monkeypatch.setattr(tmdb_client, "find_poster_by_imdb", lambda ident: "/f.jpg" if ident == "tt0133093" else "")
    monkeypatch.setattr(tmdb_client, "download_poster", lambda path: poster_bytes())
    outcome = pf.fetch_poster(meta(imdb_id="tt0133093"))
    assert outcome.found and outcome.service == "IMDb (through TMDB)"


def test_a_scene_asks_the_scene_services_and_falls_back_to_the_next(monkeypatch):
    monkeypatch.setattr(theporndb_client, "get", lambda ident, kind="scene": SceneResult(source="ThePornDB", external_id=ident))
    monkeypatch.setattr(stashdb_client, "get", lambda ident: SceneResult(source="StashDB", external_id=ident, poster_urls=["https://s/p"]))
    monkeypatch.setattr(stashdb_client, "download_cover", lambda url: poster_bytes())
    outcome = pf.fetch_poster(meta(content_type=ContentType.PORN, theporndb_id="abc-123",
                                   stashdb_id="2f5a8b0e-0a4c-4b57-8d6b-1f1d0e6a9b11"))
    assert outcome.found and outcome.service == "StashDB"
    assert any("ThePornDB" in n and "no poster" in n for n in outcome.notes)


def test_only_a_true_poster_is_used_unless_landscape_is_accepted(monkeypatch):
    monkeypatch.setattr(tmdb_client, "get_movie_details", lambda i: {"_poster_path": "/p.jpg"})
    monkeypatch.setattr(tmdb_client, "download_poster", lambda path: solid(900, 600, "PNG"))  # a landscape picture
    refused = pf.fetch_poster(meta(tmdb_id="movie:1"))
    assert not refused.found and any("TMDB" in n for n in refused.notes)
    assert pf.fetch_poster(meta(tmdb_id="movie:1"), accept_landscape=True).found


def test_a_missing_key_and_a_missing_id_are_said_plainly(monkeypatch):
    monkeypatch.setattr(api_keys, "get_key", lambda name: "")
    outcome = pf.fetch_poster(meta(tmdb_id="movie:1"))
    assert not outcome.found and "no API key" in outcome.why_not()
    assert "no source id" in pf.fetch_poster(meta()).why_not()


# -- the window flow --------------------------------------------------------------------------------------------------------


@pytest.fixture
def window(monkeypatch, tmp_path, qapp):
    import gui.main_window as mw

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")
    monkeypatch.setattr(mw.MainWindow, "_restore_last_folder_on_startup", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_check_external_tools_on_startup", lambda self: None)
    w = mw.MainWindow()
    yield w
    for vf in w.video_files:
        vf.dirty = False
    w.close()


def _files(window, tmp_path, specs):
    from core.video_file import VideoFile

    files = []
    for name, fields in specs:
        path = tmp_path / name
        path.write_bytes(b"x" * 1000)
        files.append(VideoFile(path=path, metadata=VideoMetadata(**fields)))
    window.video_files = files
    window._refresh_table_rows()
    return files


def _stub_fetch(monkeypatch):
    from core.poster import prepare_poster

    prepared = prepare_poster(poster_bytes())
    monkeypatch.setattr(pf, "fetch_poster", lambda metadata, **kw: pf.PosterOutcome(poster=prepared, service="TMDB"))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Ok)


def test_get_poster_stages_posters_for_files_with_ids_only(window, tmp_path, monkeypatch):
    from gui.poster_flow import run_get_poster

    a, b, c = _files(window, tmp_path, [("a.mp4", {"tmdb_id": "movie:1"}), ("b.mp4", {}), ("c.mp4", {"tmdb_id": "movie:2", "cover_info": "JPEG 600x900"})])
    _stub_fetch(monkeypatch)
    run_get_poster(window, [a, b, c], options=(False, False))
    assert a.metadata.cover_pending is not None and a.dirty
    assert b.metadata.cover_pending is None  # no id: nothing to take a poster from
    assert c.metadata.cover_pending is None  # already has one, Replace not ticked
    assert window.undo_manager.can_undo()
    assert "1 poster(s) staged" in window.status_bar.currentMessage()
    window.undo_last_action()
    assert a.metadata.cover_pending is None


def test_replace_asks_the_usual_review_and_a_yes_replaces(window, tmp_path, monkeypatch):
    import gui.source_apply as sa
    from gui.poster_flow import run_get_poster

    (c,) = _files(window, tmp_path, [("c.mp4", {"tmdb_id": "movie:2", "cover_info": "JPEG 600x900"})])
    _stub_fetch(monkeypatch)
    seen = {}

    def review(parent, files, changes, label):
        seen["changes"] = changes
        return changes  # the user ticks everything

    monkeypatch.setattr(sa, "resolve_overwrite_conflicts", review)
    run_get_poster(window, [c], options=(True, False))
    assert "cover_info" in seen["changes"][0] and c.metadata.cover_pending is not None


def test_no_ids_at_all_says_to_run_a_lookup_first(window, tmp_path, monkeypatch):
    from gui.poster_flow import run_get_poster

    files = _files(window, tmp_path, [("a.mp4", {})])
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda parent, title, text, *a, **k: shown.append(text))
    run_get_poster(window, files)
    assert shown and "Look Up" in shown[0]


# -- the Redact step ----------------------------------------------------------------------------------------------------------


def _ctx(metadata):
    return SimpleNamespace(work=SimpleNamespace(metadata=metadata, dirty=False), bg=lambda fn, *a, **k: fn(*a, **k),
                           env=SimpleNamespace(cancel_event=None), skip_reason="")


def test_the_step_is_in_the_catalogue_after_the_lookups_and_off_by_default():
    keys_in_order = [s.key for s in rs.build_catalogue(lambda: [])]
    assert keys_in_order.index("get_poster") == keys_in_order.index("scene_lookup") + 1
    assert not rs.PosterStep().default_enabled


def test_the_step_suggests_the_poster_of_the_files_id_and_applies_it(monkeypatch):
    from core.poster import prepare_poster

    prepared = prepare_poster(poster_bytes())
    monkeypatch.setattr(pf, "fetch_poster", lambda metadata, **kw: pf.PosterOutcome(poster=prepared, service="TMDB"))
    ctx = _ctx(meta(tmdb_id="movie:603"))
    step = rs.PosterStep()
    result = step.execute(ctx)
    assert result.confidence == rs.LOOKUP_ID and not result.review_only  # an id match: applied at the default threshold
    lines = step.apply_suggestion(ctx, result)
    assert ctx.work.metadata.cover_pending == prepared.jpeg and ctx.work.dirty and "TMDB" in lines[0]


def test_the_step_leaves_a_file_with_a_poster_or_without_ids_alone(monkeypatch):
    monkeypatch.setattr(pf, "fetch_poster", lambda *a, **k: pytest.fail("fetched"))
    step = rs.PosterStep()
    assert step.execute(_ctx(meta(tmdb_id="movie:1", cover_info="JPEG 600x900"))).note == ""
    assert step.execute(_ctx(meta(tmdb_id="movie:1", cover_pending=b"x"))).note == ""
    assert step.execute(_ctx(meta())).note == ""


def test_the_step_says_why_when_no_poster_was_found(monkeypatch):
    monkeypatch.setattr(pf, "fetch_poster", lambda metadata, **kw: pf.PosterOutcome(notes=["TMDB: the record has no poster"]))
    result = rs.PosterStep().execute(_ctx(meta(tmdb_id="movie:1")))
    assert "the record has no poster" in result.note
