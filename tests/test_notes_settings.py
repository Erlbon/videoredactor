"""What Create Notes remembers (core/notes_settings.py), its Preferences page, the lookup cache's place and the settings bundle."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from core import config, notes_settings  # noqa: E402
from core.settings_adapter import SECTIONS  # noqa: E402

_app = QApplication.instance() or QApplication([])


def test_the_defaults_are_both_kinds_cache_on_and_wikipedia_on():
    assert notes_settings.kinds() == ("series", "movie") and notes_settings.cache_enabled() and notes_settings.wikipedia_enabled()
    assert notes_settings.last_folder() == "" and notes_settings.dialog_size() == ""


def test_the_choices_round_trip_through_the_settings_file():
    notes_settings.set_kinds(["movie"])
    notes_settings.set_cache_enabled(False)
    notes_settings.set_wikipedia_enabled(False)
    notes_settings.set_last_folder("D:\\Films")
    notes_settings.set_dialog_size("1200x800")
    assert notes_settings.kinds() == ("movie",) and not notes_settings.cache_enabled() and not notes_settings.wikipedia_enabled()
    assert notes_settings.last_folder() == "D:\\Films" and notes_settings.dialog_size() == "1200x800"
    assert config.get_setting("notes", "cache", "") == "0"


@pytest.mark.parametrize("text,expected", [
    ("series", ("series",)), ("movie", ("movie",)), ("both", ("series", "movie")), ("movie,series", ("series", "movie")),
    ("films", ("movie",)), ("", ("series", "movie")), ("nonsense", ("series", "movie")), (["movie"], ("movie",)),
])
def test_kinds_are_read_forgivingly_and_always_in_the_same_order(text, expected):
    assert notes_settings.parse_kinds(text) == expected
    assert notes_settings.kinds_text(text) == ",".join(expected)


def test_a_garbled_flag_means_the_default():
    config.set_setting("notes", "cache", "maybe")
    assert notes_settings.cache_enabled() is True  # not one of the off words: the default (on)
    config.set_setting("notes", "cache", "off")
    assert notes_settings.cache_enabled() is False


def test_the_cache_lives_next_to_the_settings_file_never_in_a_library(monkeypatch, tmp_path):
    from redactor_common.core import notes_cache as shared

    monkeypatch.undo()  # the autouse fixture points the cache at a temp folder; this test wants the real rule
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "videoredactor_settings.ini")
    assert notes_settings.cache_path() == tmp_path / notes_settings.CACHE_FILE_NAME
    monkeypatch.setattr(shared, "_default", None)
    cache = notes_settings.default_cache(True)
    cache.put("k", b'"v"')
    assert (tmp_path / notes_settings.CACHE_FILE_NAME).is_file() and notes_settings.clear_cache() == 1
    assert notes_settings.default_cache(False) is None
    cache.close()


def test_the_preferences_page_has_the_two_switches_and_a_clear_button(monkeypatch):
    from gui.video_preferences import CLEAR_NOTES_CACHE_LABEL, VideoPreferencesDialog

    said = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: said.append(a[2]) or QMessageBox.StandardButton.Ok))
    dlg = VideoPreferencesDialog()
    assert "Create Notes" in dlg.page_titles()
    assert dlg.value("notes_cache") is True and dlg.value("notes_wikipedia") is True
    from gui.video_preferences import clear_notes_cache_action

    assert CLEAR_NOTES_CACHE_LABEL and clear_notes_cache_action(dlg) is False and "already empty" in said[-1]
    notes_settings.default_cache(True).put("k", b'"v"')
    assert clear_notes_cache_action(dlg) is True and "1 saved answer removed" in said[-1]


def test_the_settings_bundle_carries_the_notes_choices_but_not_the_machine_folder():
    keys = {(k.ini_section, k.ini_key): portable for _l, portable, ks in SECTIONS.values() for k in ks}
    assert keys[("notes", "cache")] and keys[("notes", "wikipedia")] and keys[("notes", "kinds")]
    section = next(portable for key, (_l, portable, ks) in SECTIONS.items() if any(k.ini_key == "last_folder" and k.ini_section == "notes" for k in ks))
    assert section is False  # the last folder travels with the other machine-specific folders, unticked by default
