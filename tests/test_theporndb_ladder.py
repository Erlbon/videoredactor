"""The structured ThePornDB search ladder (2026-10-18): the request each rung sends (title / date / site / performers /
year / q, the deepObject encoding of performers), stop at the first rung with results, never an empty filter, errors end
the ladder, the plain-text switch, /movies, and what the dialog says. Fake fetch layer only: no live service was used,
so the live behaviour of the filters is unproven."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import urllib.error  # noqa: E402
import urllib.parse  # noqa: E402

import pytest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from core import theporndb_client as tp  # noqa: E402
from core.scene_lookup import ladder_note  # noqa: E402
from gui.scene_search_dialog import SceneSearchDialog, theporndb_source  # noqa: E402
from tests.scene_fixtures import KEY, FakeFetch, http_error, tpdb_scene  # noqa: E402
from tests.test_scene_folder_name import make_window_file  # noqa: E402
from tests.test_scene_search_dialog import window  # noqa: E402,F401

_app = QApplication.instance() or QApplication([])

EMPTY = {"data": []}
ONE = {"data": [tpdb_scene()]}
FIELDS = {"title": "Strap Some Boyz 2", "date": "2014-02-17", "year": "2014", "studio": "Evil Angel",
          "performers": ["Mia Malkova", "Riley Reid"]}


@pytest.fixture
def fetch(monkeypatch):
    monkeypatch.setenv("THEPORNDB_API_KEY", KEY)
    monkeypatch.setattr(tp, "_sleep", lambda seconds: None)

    def install(*replies):
        fake = FakeFetch(*replies)
        monkeypatch.setattr(tp, "_fetch", fake)
        return fake

    return install


def parts(request):
    split = urllib.parse.urlsplit(request.full_url)
    return split.path, dict(urllib.parse.parse_qsl(split.query))


def test_step_1_is_title_plus_the_exact_date(fetch):
    fake = fetch(ONE)
    found, label = tp.search_structured(FIELDS)
    path, query = parts(fake.requests[0])
    assert path == "/scenes"
    assert query == {"title": "Strap Some Boyz 2", "date": "2014-02-17", "date_operation": "=", "per_page": "25"}
    assert len(found) == 1 and label == "title + date: 2014-02-17" and len(fake.requests) == 1


def test_it_stops_at_the_first_rung_with_results_and_each_rung_relaxes_the_one_before(fetch):
    fake = fetch(EMPTY, EMPTY, ONE)
    trace = []
    found, label = tp.search_structured(FIELDS, trace=trace)
    assert len(fake.requests) == 3 and label == "title + year: 2014"
    queries = [parts(r)[1] for r in fake.requests]
    assert "date" in queries[0] and "site" in queries[1] and "date" not in queries[2] and "site" not in queries[2]
    assert queries[2] == {"title": "Strap Some Boyz 2", "year": "2014", "per_page": "25"}
    assert trace == [(1, 4, "title + date: 2014-02-17", 0), (2, 4, "title + studio: Evil Angel + performers", 0),
                     (3, 4, "title + year: 2014", 1)]


def test_step_2_sends_the_site_with_all_levels_and_the_performers_as_a_deep_object(fetch):
    fake = fetch(ONE)
    tp.search_structured({**FIELDS, "date": ""})
    request = fake.requests[0]
    query = parts(request)[1]
    assert query["title"] == "Strap Some Boyz 2" and query["site"] == "Evil Angel"
    assert query["site_operation"] == "Site/Parent/Network"
    assert query["performers[0]"] == "Mia Malkova" and query["performers[1]"] == "Riley Reid"
    assert "performer_and" not in query
    assert "performers%5B0%5D=Mia+Malkova" in request.full_url  # the brackets are percent-encoded, as a deepObject is


def test_step_4_is_the_old_plain_q_search(fetch):
    fake = fetch(EMPTY, EMPTY, EMPTY, ONE)
    found, label = tp.search_structured(FIELDS)
    assert label == "plain text search"
    assert parts(fake.requests[3])[1] == {"q": "Strap Some Boyz 2", "year": "2014", "per_page": "25"}


def test_without_a_date_a_studio_or_performers_only_title_and_plain_text_run_and_no_filter_is_empty(fetch):
    fake = fetch(EMPTY, EMPTY)
    found, label = tp.search_structured({"title": "Strap Some Boyz 2"})
    assert (found, label) == ([], "")
    queries = [parts(r)[1] for r in fake.requests]
    assert queries == [{"title": "Strap Some Boyz 2", "per_page": "25"}, {"q": "Strap Some Boyz 2", "per_page": "25"}]
    for query in queries:
        assert all(value != "" for value in query.values())


def test_nothing_to_search_for_sends_nothing(fetch):
    fake = fetch(ONE)
    assert tp.search_structured({"title": "  ", "studio": "Evil Angel"}) == ([], "")
    assert fake.requests == []


def test_performers_and_a_site_without_a_title(fetch):
    fake = fetch(ONE)
    found, label = tp.search_structured({"title": "", "studio": "Evil Angel", "performers": ["Mia Malkova"]})
    query = parts(fake.requests[0])[1]
    assert label == "performers + studio: Evil Angel"
    assert query == {"performers[0]": "Mia Malkova", "site": "Evil Angel", "site_operation": "Site/Parent/Network",
                     "per_page": "25"}


def test_the_title_is_cut_at_200_characters_and_at_most_four_performers_are_sent(fetch):
    fake = fetch(ONE)
    tp.search_structured({"title": "x" * 300, "date": "2014-02-17", "performers": [f"Name {i}" for i in range(9)]})
    assert len(parts(fake.requests[0])[1]["title"]) == 200
    fake2 = fetch(ONE)
    tp.search_structured({"title": "T", "studio": "S", "performers": [f"Name {i}" for i in range(9)]})
    assert len([k for k in parts(fake2.requests[0])[1] if k.startswith("performers[")]) == 4


def test_an_invalid_date_or_year_is_not_sent(fetch):
    fake = fetch(EMPTY, EMPTY)
    tp.search_structured({"title": "T", "date": "2014-2-17", "year": "abc"})
    assert all("date" not in parts(r)[1] and "year" not in parts(r)[1] for r in fake.requests)


def test_plain_only_runs_just_the_q_rung_and_movies_use_the_movies_path(fetch):
    fake = fetch(ONE)
    tp.search_structured(FIELDS, kind="movie", plain_only=True)
    assert len(fake.requests) == 1
    path, query = parts(fake.requests[0])
    assert path == "/movies" and query["q"] == "Strap Some Boyz 2"


def test_the_ladder_runs_for_movies_too(fetch):
    fake = fetch(EMPTY, ONE)
    found, label = tp.search_structured(FIELDS, kind="movie")
    assert [parts(r)[0] for r in fake.requests] == ["/movies", "/movies"] and found[0].kind == "movie"


def test_an_auth_error_ends_the_ladder(fetch):
    fake = fetch(http_error(401, b'{"message": "Unauthenticated."}'))
    with pytest.raises(tp.ThePornDBAuthError):
        tp.search_structured(FIELDS)
    assert len(fake.requests) == 1


def test_a_network_error_ends_the_ladder(fetch):
    fake = fetch(urllib.error.URLError("down"))
    with pytest.raises(tp.ThePornDBError):
        tp.search_structured(FIELDS)
    assert len(fake.requests) == 1


def test_ladder_note_texts():
    trace = [(1, 4, "title + date: 2014-02-17", 0), (2, 4, "title + studio: Evil Angel", 3)]
    assert ladder_note(trace, "title + studio: Evil Angel", 3) == (
        "Found 3 with title + studio: Evil Angel (step 2 of 4). Before that: step 1 of 4 title + date: 2014-02-17 found 0.")
    assert ladder_note(trace[:1], "x", 0).startswith("Nothing found. Tried: step 1 of 4 title + date: 2014-02-17 found 0")
    assert ladder_note([], "", 0).startswith("Nothing to search for")


# -- the dialog -------------------------------------------------------------------------------------------------------------

def _dialog(window, tmp_path, name="2014-02-17 Strap Some Boyz 2 - Scene 3.mp4", folder="Evil Angel"):
    vf = make_window_file(window, tmp_path, folder, name)
    return SceneSearchDialog(theporndb_source(), vf, parent=window, auto_search=False)


def test_the_dialog_runs_the_ladder_and_says_which_step_answered(window, tmp_path, fetch):
    fake = fetch(EMPTY, ONE)
    dialog = _dialog(window, tmp_path)
    dialog._on_search()
    assert len(fake.requests) == 2
    assert parts(fake.requests[0])[1]["date"] == "2014-02-17"
    status = dialog.status_label.text()
    assert "Found 1 with title + studio: Evil Angel (step 2 of 4)" in status
    assert "Before that: step 1 of 4 title + date: 2014-02-17 found 0" in status
    assert len(dialog.results) == 1


def test_the_dialog_lists_every_step_tried_when_nothing_answers(window, tmp_path, fetch):
    fetch(EMPTY)
    dialog = _dialog(window, tmp_path)
    dialog._on_search()
    status = dialog.status_label.text()
    assert "Nothing found. Tried: step 1 of 4" in status and "step 4 of 4 plain text search found 0" in status
    assert status.count("No results found") == 0 and dialog.results == []


def test_an_edited_title_uses_the_guessed_date_and_studio_but_the_edited_year_drops_the_date(window, tmp_path, fetch):
    fake = fetch(ONE)
    dialog = _dialog(window, tmp_path)
    dialog.query_edit.setText("Strap Some Boyz Two")
    dialog._on_search()
    assert parts(fake.requests[0])[1]["title"] == "Strap Some Boyz Two" and parts(fake.requests[0])[1]["date"] == "2014-02-17"
    fake2 = fetch(ONE)
    dialog.year_edit.setText("2013")
    dialog._on_search()
    assert "date" not in parts(fake2.requests[0])[1]


def test_the_plain_text_box_forces_the_q_search_only(window, tmp_path, fetch):
    fake = fetch(ONE)
    dialog = _dialog(window, tmp_path)
    assert dialog.plain_box is not None
    dialog.plain_box.setChecked(True)
    dialog._on_search()
    assert len(fake.requests) == 1 and "q" in parts(fake.requests[0])[1]
    assert "title" not in parts(fake.requests[0])[1]


def test_the_performers_from_the_name_reach_the_request(window, tmp_path, fetch):
    fake = fetch(EMPTY, ONE)
    dialog = _dialog(window, tmp_path, name="AnalOilLatex_s01_CharlotteSartre_RamonNomar_480p.mp4", folder="Anal Oil Latex (Evil Angel) 2022")
    dialog._on_search()
    query = parts(fake.requests[0])[1]
    assert query["title"] == "Anal Oil Latex" and query["site"] == "Evil Angel"
    assert query["performers[0]"] == "Charlotte Sartre" and query["performers[1]"] == "Ramon Nomar"
    assert "date" not in query


def test_a_source_without_the_ladder_keeps_the_single_search(window, tmp_path):
    from tests.test_scene_search_dialog import make_source

    vf = make_window_file(window, tmp_path, "Evil Angel", "2014-02-17 Strap Some Boyz 2.mp4")
    source = make_source(by_title=[])
    dialog = SceneSearchDialog(source, vf, parent=window, auto_search=False)
    assert dialog.plain_box is None
    dialog._on_search()
    assert source.calls["title"][0][0] == "Strap Some Boyz 2"
