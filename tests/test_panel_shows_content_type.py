"""The side panel's Content Type box shows the selected file's value (a str-Enum looked up by its plain text)."""
from core.video_metadata import ContentType
from tests.test_clear_fields import NAMES, select
from tests.test_per_file_confirmation import close, make_window


def test_the_panel_shows_the_selected_files_content_type(monkeypatch, tmp_path):
    w = make_window(monkeypatch, tmp_path, NAMES[:2])
    first, second = w.video_files
    first.metadata.content_type = ContentType.MOVIE
    second.metadata.content_type = ContentType.TV
    combo = w.tag_panel._inputs["content_type"]
    select(w, first)
    assert combo.currentText() == "Movie"
    select(w, second)
    assert combo.currentText() == "TV"
    select(w, first, second)
    assert combo.currentText() == ""  # mixed selection stays blank
    close(w)
