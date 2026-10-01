"""Redact's lookup step with a local IMDb database: it is asked FIRST (offline), TMDB/TheTVDB only for what is
still empty; confidence never above the online rules for the same evidence; empty fields only; a missing
database is a note, not a failure. Synthetic data; no network."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from redactor_common.core.local_db import forget_cached  # noqa: E402
from redactor_common.core.pipeline import FileStatus  # noqa: E402

from core import imdb_local, tmdb_client, tvdb_client  # noqa: E402
from core import redact_steps as rs  # noqa: E402
from core.video_metadata import ContentType  # noqa: E402
from tests.imdb_fixture import AKAS, BASICS, EPISODES, RATINGS, build  # noqa: E402
from tests.test_imdb_local import OFFICE, OFFICE_EPISODES, OFFICE_RATINGS  # noqa: E402
from tests.test_redact_steps import (  # noqa: E402,F401
    bin_, fake_tmdb, lookup_entry, make_env, movie, show, stub, work,
)


@pytest.fixture
def imdb(tmp_path):
    path, _s, _f = build(tmp_path, basics=BASICS + OFFICE, ratings=RATINGS + OFFICE_RATINGS,
                         episodes=EPISODES + OFFICE_EPISODES, akas=AKAS)
    yield path
    forget_cached(path)


def env_with(bin_, tmp_path, path):
    env = make_env(bin_, tmp_path)
    env.imdb_local = path
    return env


def applied_text(entry):
    return "\n".join(entry.applied)


def no_online(monkeypatch):
    """TMDB and TheTVDB must not be asked at all."""
    for module, names in ((tmdb_client, ("search_movies", "search_tv")), (tvdb_client, ("search_series",))):
        for name in names:
            monkeypatch.setattr(module, name, lambda *a, **k: pytest.fail("the online lookup was used"))


def tmdb_unreachable(monkeypatch):
    def offline(*a, **k):
        raise tmdb_client.TMDBError("Couldn't reach TMDB (timed out).")

    monkeypatch.setattr(tmdb_client, "search_movies", offline)
    monkeypatch.setattr(tmdb_client, "search_tv", offline)

    def tvdb_offline(*a, **k):
        raise tvdb_client.TVDBError("No TheTVDB API key configured.")

    monkeypatch.setattr(tvdb_client, "search_series", tvdb_offline)


# --- films, offline -------------------------------------------------------------------------------


def test_an_exact_imdb_id_in_the_filename_is_97_percent(work, bin_, tmp_path, monkeypatch, imdb):
    tmdb_unreachable(monkeypatch)
    entry = lookup_entry(stub(work, "Some Film (1984) {imdb-tt90000001}.mp4"), env_with(bin_, tmp_path, imdb))
    assert entry.status is FileStatus.CHANGED and entry.review == []
    text = applied_text(entry)
    assert "auto-applied at 97%" in text and "from IMDb (local database) 'Zarnak (1984)'" in text
    assert "title = 'Zarnak'" in text and "release_date = '1984'" in text
    assert "genre_tags = 'Adventure, Drama, Science Fiction'" in text and "content_type = 'Movie'" in text
    assert any("TMDB lookup unavailable" in n for n in entry.notes)  # offline: said so, not failed
    assert entry.failures == []


def test_an_id_in_the_comment_field_is_used_too(work, bin_, tmp_path, monkeypatch, imdb):
    tmdb_unreachable(monkeypatch)
    vf = stub(work, "movie.mp4", comment="https://www.imdb.com/title/tt90000002/")
    entry = lookup_entry(vf, env_with(bin_, tmp_path, imdb))
    assert entry.status is FileStatus.CHANGED and "release_date = '2021'" in applied_text(entry)


def test_an_unknown_id_is_a_note_and_the_normal_lookup_carries_on(work, bin_, tmp_path, monkeypatch, imdb):
    tmdb_unreachable(monkeypatch)
    entry = lookup_entry(stub(work, "Zarnak.1984.tt90000999.mp4"), env_with(bin_, tmp_path, imdb))
    assert any("doesn't have tt90000999" in n for n in entry.notes)
    assert entry.status is FileStatus.CHANGED and "release_date = '1984'" in applied_text(entry)


def test_title_and_year_pick_the_right_zarnak(work, bin_, tmp_path, monkeypatch, imdb):
    tmdb_unreachable(monkeypatch)
    old = lookup_entry(stub(work, "Zarnak.1984.1080p.mp4"), env_with(bin_, tmp_path, imdb))
    assert old.status is FileStatus.CHANGED and "auto-applied at 95%" in applied_text(old)
    assert "release_date = '1984'" in applied_text(old) and "IMDb tt90000001" not in applied_text(old)
    new = lookup_entry(stub(work, "Zarnak.2021.mp4"), env_with(bin_, tmp_path, imdb))
    assert "release_date = '2021'" in applied_text(new) and "Action, Adventure, Drama" in applied_text(new)


def test_a_wrong_or_off_by_one_year_needs_review(work, bin_, tmp_path, monkeypatch, imdb):
    tmdb_unreachable(monkeypatch)
    for name in ("Zarnak.1999.mp4", "Zarnak.1985.mp4"):
        entry = lookup_entry(stub(work, name), env_with(bin_, tmp_path, imdb))
        assert entry.status is FileStatus.NEEDS_REVIEW and entry.applied == [], name
        assert entry.review[0].confidence == pytest.approx(0.6) and "not the year" in entry.review[0].reason


def test_a_different_title_is_the_closest_result_40_percent(work, bin_, tmp_path, monkeypatch, imdb):
    tmdb_unreachable(monkeypatch)
    entry = lookup_entry(stub(work, "Zarnak.Drifter.2020.mp4"), env_with(bin_, tmp_path, imdb))
    assert entry.status is FileStatus.NEEDS_REVIEW or any("found no film" in n for n in entry.notes)
    if entry.review:
        assert entry.review[0].confidence == pytest.approx(0.4)


def test_a_localized_title_resolves_through_the_alternative_titles(work, bin_, tmp_path, monkeypatch, imdb):
    tmdb_unreachable(monkeypatch)
    entry = lookup_entry(stub(work, "Il codice della brace (1986).mp4"), env_with(bin_, tmp_path, imdb))
    assert entry.status is FileStatus.CHANGED
    text = applied_text(entry)
    assert "title = 'The Ember Cipher'" in text and "release_date = '1986'" in text


def test_nothing_found_locally_is_a_note_and_the_online_lookup_runs(work, bin_, tmp_path, monkeypatch, imdb):
    fake_tmdb(monkeypatch, movies=[movie("Inception", 2010)])
    entry = lookup_entry(stub(work, "Inception.2010.mp4"), env_with(bin_, tmp_path, imdb))
    assert entry.status is FileStatus.CHANGED and "from TMDB 'Inception (2010)'" in applied_text(entry)
    assert any("IMDb (local database) found no film for 'Inception'" in n for n in entry.notes)


# --- TV -------------------------------------------------------------------------------------------


def test_an_episode_resolves_through_the_series_and_numbers(work, bin_, tmp_path, monkeypatch, imdb):
    tmdb_unreachable(monkeypatch)
    entry = lookup_entry(stub(work, "Quillfeather S01E02.mp4"), env_with(bin_, tmp_path, imdb))
    # the one show with that title + a real S01E02: 95% (a bare title match would be 80%)
    assert entry.status is FileStatus.CHANGED and "auto-applied at 95%" in applied_text(entry)
    text = applied_text(entry)
    assert "title = \"Sparrow's Dilemma...\"" in text or "title = 'Cat\\'s" in text or "Sparrow's Dilemma" in text
    assert "show_title = 'Quillfeather'" in text and "season_number = 1" in text and "episode_number = 2" in text
    assert "release_date = '2008'" in text and "content_type = 'TV'" in text


def test_a_series_title_only_when_the_episode_is_missing_is_80_percent(work, bin_, tmp_path, monkeypatch, imdb):
    tmdb_unreachable(monkeypatch)
    entry = lookup_entry(stub(work, "Quillfeather S09E99.mp4"), env_with(bin_, tmp_path, imdb))
    assert entry.status is FileStatus.NEEDS_REVIEW
    assert entry.review[0].confidence == pytest.approx(0.8) and "no year to confirm" in entry.review[0].reason
    assert any("no S09E99" in n for n in entry.notes)


def test_two_series_with_the_title_are_ambiguous_unless_the_year_agrees(work, bin_, tmp_path, monkeypatch, imdb):
    tmdb_unreachable(monkeypatch)
    entry = lookup_entry(stub(work, "Harbor Lights S02E05.mp4"), env_with(bin_, tmp_path, imdb))
    assert entry.status is FileStatus.NEEDS_REVIEW and entry.review[0].confidence == pytest.approx(0.6)
    entry = lookup_entry(stub(work, "Harbor Lights (2005) S02E05.mp4"), env_with(bin_, tmp_path, imdb))
    assert entry.status is FileStatus.CHANGED and "title = 'Moulting'" in applied_text(entry)
    entry = lookup_entry(stub(work, "Harbor Lights (2001) S02E05.mp4"), env_with(bin_, tmp_path, imdb))
    assert any("no S02E05 for 'Harbor Lights'" in n for n in entry.notes)  # the UK series has no such episode


def test_an_episode_imdb_id_gives_series_and_numbers(work, bin_, tmp_path, monkeypatch, imdb):
    tmdb_unreachable(monkeypatch)
    entry = lookup_entry(stub(work, "episode tt90000022.mp4"), env_with(bin_, tmp_path, imdb))
    text = applied_text(entry)
    assert entry.status is FileStatus.CHANGED and "auto-applied at 97%" in text
    assert "show_title = 'Harbor Lights'" in text and "season_number = 2" in text and "episode_number = 5" in text


# --- local first, online for the rest --------------------------------------------------------------


def test_local_fields_win_and_tmdb_fills_only_what_is_left(work, bin_, tmp_path, monkeypatch, imdb):
    fake_tmdb(monkeypatch, movies=[movie("Zarnak", 1984)])
    monkeypatch.setattr(tmdb_client, "get_movie_details", lambda i: {
        "title": "Zarnak (TMDB spelling)", "description": "Spice.", "genre_tags": "Sci-Fi", "release_date": "1984-12-14",
        "language": "en", "director": "Lynch", "cast": "MacLachlan", "studio": "Dino", "_poster_path": None})
    vf = stub(work, "Zarnak.1984.mp4", director="Mine")
    entry = lookup_entry(vf, env_with(bin_, tmp_path, imdb))
    assert entry.status is FileStatus.CHANGED
    text = applied_text(entry)
    assert "from IMDb (local database) + TMDB 'Zarnak (1984)'" in text
    assert "title = 'Zarnak'" in text and "TMDB spelling" not in text            # IMDb asked first, wins
    assert "genre_tags = 'Adventure, Drama, Science Fiction'" in text           # not TMDB's "Sci-Fi"
    assert "release_date = '1984-12-14'" in text                                # the fuller date beats a bare year
    assert "description = 'Spice.'" in text and "cast = 'MacLachlan'" in text and "studio = 'Dino'" in text
    assert "director" not in text                                              # never overwrites


def test_tmdb_is_not_asked_when_the_local_fields_cover_everything_left(work, bin_, tmp_path, monkeypatch, imdb):
    no_online(monkeypatch)
    vf = stub(work, "Zarnak.1984.mp4", description="d", director="x", cast="c", studio="s", language="en")
    entry = lookup_entry(vf, env_with(bin_, tmp_path, imdb))
    assert entry.status is FileStatus.CHANGED and "title = 'Zarnak'" in applied_text(entry)
    assert not any("TMDB" in n for n in entry.notes)


def test_a_local_match_never_overwrites_a_filled_field(work, bin_, tmp_path, monkeypatch, imdb):
    tmdb_unreachable(monkeypatch)
    vf = stub(work, "Zarnak.1984.mp4", title="My Title", genre_tags="Noir", release_date="1984-06-01")
    ctx = rs.VideoCtx(vf, env_with(bin_, tmp_path, imdb))
    step = rs.LookupStep()
    result = step.run(ctx)
    step.apply_suggestion(ctx, result)
    md = ctx.work.metadata
    assert (md.title, md.genre_tags, md.release_date) == ("My Title", "Noir", "1984-06-01")
    assert md.content_type is ContentType.MOVIE


def test_the_combined_confidence_is_the_lower_of_the_two(work, bin_, tmp_path, monkeypatch, imdb):
    # IMDb is certain (title and year match exactly) but TMDB finds two equally good films: reviewed.
    fake_tmdb(monkeypatch, movies=[movie("Zarnak", 1984, 1), movie("Zarnak", 1984, 2)])
    entry = lookup_entry(stub(work, "Zarnak.1984.mp4"), env_with(bin_, tmp_path, imdb))
    assert entry.status is FileStatus.NEEDS_REVIEW
    assert entry.review[0].confidence == pytest.approx(0.6) and "IMDb (local database) + TMDB" in str(entry.review[0].value)


def test_a_tmdb_match_of_another_year_is_not_mixed_in(work, bin_, tmp_path, monkeypatch, imdb):
    fake_tmdb(monkeypatch, movies=[movie("Zarnak", 2021)])
    entry = lookup_entry(stub(work, "Zarnak.1984.mp4"), env_with(bin_, tmp_path, imdb))
    text = applied_text(entry)
    assert entry.status is FileStatus.CHANGED and "from IMDb (local database) 'Zarnak (1984)'" in text
    assert "description" not in text and "director" not in text
    assert any("different year" in n for n in entry.notes)


def test_tv_local_first_then_tmdb_for_the_rest(work, bin_, tmp_path, monkeypatch, imdb):
    fake_tmdb(monkeypatch, shows=[show("Quillfeather", 2008)], episodes={"title": "TMDB ep", "description": "Ep."})
    monkeypatch.setattr(tmdb_client, "get_tv_show_details", lambda i: {
        "show_title": "Quillfeather", "description": "Chemistry.", "genre_tags": "Drama", "release_date": "2008-01-20",
        "network": "AMC", "_poster_path": None})
    entry = lookup_entry(stub(work, "Quillfeather (2008) S01E02.mp4"), env_with(bin_, tmp_path, imdb))
    text = applied_text(entry)
    assert entry.status is FileStatus.CHANGED and "IMDb (local database) + TMDB" in text
    assert "Sparrow's Dilemma" in text and "TMDB ep" not in text        # the episode title is IMDb's
    assert "description = 'Ep.'" in text and "network = 'AMC'" in text   # plot and network from TMDB


def test_thetvdb_still_backs_up_a_local_tv_match(work, bin_, tmp_path, monkeypatch, imdb):
    def no_key(*a, **k):
        raise tmdb_client.TMDBError("No TMDB API key configured.")

    monkeypatch.setattr(tmdb_client, "search_tv", no_key)
    monkeypatch.setattr(tvdb_client, "search_series", lambda title: [
        tvdb_client.SeriesCandidate(9, "Quillfeather", "2008-01-20", "", None)])
    monkeypatch.setattr(tvdb_client, "get_series_details", lambda i: {
        "show_title": "Quillfeather", "description": "Chem.", "genre_tags": "Drama", "release_date": "",
        "network": "AMC", "_poster_path": None})
    monkeypatch.setattr(tvdb_client, "get_episode_details", lambda i, s, e: {
        "title": "Cats", "description": "", "release_date": "2008-01-27", "season_number": s, "episode_number": e})
    entry = lookup_entry(stub(work, "Quillfeather (2008) S01E02.mp4"), env_with(bin_, tmp_path, imdb))
    text = applied_text(entry)
    assert "IMDb (local database) + TheTVDB" in text and "network = 'AMC'" in text and "Sparrow's Dilemma" in text
    assert any("No TMDB API key" in n for n in entry.notes)


# --- no / broken database -------------------------------------------------------------------------


def test_without_a_configured_database_nothing_local_is_touched(work, bin_, tmp_path, monkeypatch):
    monkeypatch.setattr(imdb_local, "open_database", lambda p: pytest.fail("the local database was opened"))
    fake_tmdb(monkeypatch, movies=[movie("Inception", 2010)])
    entry = lookup_entry(stub(work, "Inception.2010.mp4"), make_env(bin_, tmp_path))
    assert entry.status is FileStatus.CHANGED and "from TMDB" in applied_text(entry)
    assert not any("IMDb" in n for n in entry.notes)


def test_a_missing_database_file_is_a_note_not_a_failure(work, bin_, tmp_path, monkeypatch):
    fake_tmdb(monkeypatch, movies=[movie("Inception", 2010)])
    env = env_with(bin_, tmp_path, str(tmp_path / "gone.db"))
    entry = lookup_entry(stub(work, "Inception.2010.mp4"), env)
    assert entry.status is FileStatus.CHANGED and entry.failures == []
    assert "from TMDB" in applied_text(entry)
    assert any("local IMDb database is unavailable" in n for n in entry.notes)


def test_a_foreign_file_as_the_database_is_a_note_too(work, bin_, tmp_path, monkeypatch):
    other = tmp_path / "other.db"
    other.write_bytes(b"this is not sqlite")
    tmdb_unreachable(monkeypatch)
    entry = lookup_entry(stub(work, "Zarnak.1984.mp4"), env_with(bin_, tmp_path, str(other)))
    assert entry.status is FileStatus.UNCHANGED and entry.failures == []
    assert any("unavailable" in n for n in entry.notes) and any("TMDB lookup unavailable" in n for n in entry.notes)


def test_nothing_online_and_nothing_local_is_just_notes(work, bin_, tmp_path, monkeypatch, imdb):
    tmdb_unreachable(monkeypatch)
    entry = lookup_entry(stub(work, "Zzyzx.1999.mp4"), env_with(bin_, tmp_path, imdb))
    assert entry.status is FileStatus.UNCHANGED and entry.failures == []
    assert any("found no film" in n for n in entry.notes) and any("unavailable" in n for n in entry.notes)


def test_options_switch_the_local_lookup_off_per_kind(work, bin_, tmp_path, monkeypatch, imdb):
    no_online(monkeypatch)
    entry = lookup_entry(stub(work, "Zarnak.1984.mp4"), env_with(bin_, tmp_path, imdb),
                         options={"lookup": {"movies": False}})
    assert entry.status is FileStatus.UNCHANGED
    entry = lookup_entry(stub(work, "Zarnak (1984) tt90000001.mp4"), env_with(bin_, tmp_path, imdb),
                         options={"lookup": {"movies": False}})
    assert entry.status is FileStatus.UNCHANGED


def test_the_step_text_mentions_the_local_database():
    step = rs.LookupStep()
    assert "IMDb" in step.label and "Tools > IMDb Database" in step.description
    assert rs.LOOKUP_ID == 0.97 and rs.LOOKUP_ID > rs.LOOKUP_EXACT > rs.LOOKUP_TITLE_ONLY > rs.LOOKUP_AMBIGUOUS
