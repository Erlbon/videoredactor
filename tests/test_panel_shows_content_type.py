"""The side panel: the Content Type box shows the selection's value, and the panel shows only the fields that belong to
that type (dynamic field visibility), keeping any other field that holds a value."""
from core.video_metadata import ContentType
from tests.test_clear_fields import NAMES, select
from tests.test_per_file_confirmation import close, make_window


def _combo(w):
    return w.tag_panel._inputs["content_type"]  # looked up each time: a type change rebuilds the form


def _shown(w):
    return set(w.tag_panel._inputs)


def test_the_panel_shows_the_selected_files_content_type(monkeypatch, tmp_path):
    w = make_window(monkeypatch, tmp_path, NAMES[:2])
    first, second = w.video_files
    first.metadata.content_type = ContentType.MOVIE
    second.metadata.content_type = ContentType.TV
    select(w, first)
    assert _combo(w).currentText() == "Movie"
    select(w, second)
    assert _combo(w).currentText() == "TV"
    select(w, first, second)
    assert _combo(w).currentText() == ""  # mixed selection stays blank
    close(w)


def test_each_type_shows_only_its_own_fields_and_mixed_shows_all(monkeypatch, tmp_path):
    w = make_window(monkeypatch, tmp_path, NAMES[:3])
    movie, tv, unset = w.video_files
    movie.metadata.content_type = ContentType.MOVIE
    tv.metadata.content_type = ContentType.TV
    select(w, movie)
    shown = _shown(w)
    assert "director" in shown and "title" in shown and "show_title" not in shown and "artist" not in shown
    select(w, tv)
    shown = _shown(w)
    assert "show_title" in shown and "season_number" in shown and "director" not in shown
    select(w, movie, tv)
    assert {"director", "show_title", "artist"} <= _shown(w)  # mixed types: everything
    select(w, unset)
    assert {"director", "show_title", "artist"} <= _shown(w)  # type not set: everything
    close(w)


def test_a_field_that_holds_a_value_stays_visible(monkeypatch, tmp_path):
    w = make_window(monkeypatch, tmp_path, NAMES[:1])
    movie = w.video_files[0]
    movie.metadata.content_type = ContentType.MOVIE
    movie.metadata.network = "HBO"
    select(w, movie)
    assert "network" in _shown(w) and "show_title" not in _shown(w)
    assert w.tag_panel._inputs["network"].text() == "HBO"
    close(w)


def test_show_all_fields_overrides_and_back(monkeypatch, tmp_path):
    w = make_window(monkeypatch, tmp_path, NAMES[:1])
    movie = w.video_files[0]
    movie.metadata.content_type = ContentType.MOVIE
    select(w, movie)
    assert "show_title" not in _shown(w)
    w.tag_panel.show_all_box.setChecked(True)
    assert "show_title" in _shown(w) and _combo(w).currentText() == "Movie"
    w.tag_panel.show_all_box.setChecked(False)
    assert "show_title" not in _shown(w) and _combo(w).currentText() == "Movie"
    close(w)


def test_changing_the_type_in_the_panel_changes_the_fields_after_apply(monkeypatch, tmp_path):
    w = make_window(monkeypatch, tmp_path, NAMES[:1])
    vf = w.video_files[0]
    vf.metadata.content_type = ContentType.MOVIE
    select(w, vf)
    assert "show_title" not in _shown(w)
    w._on_apply_to_selected({"content_type": "TV"})
    assert vf.metadata.content_type is ContentType.TV
    assert "show_title" in _shown(w) and "director" not in _shown(w) and _combo(w).currentText() == "TV"
    close(w)
