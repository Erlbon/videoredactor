"""Every lookup in the Metadata > Look Up menu is also in the row right-click menu,
and the zero-pad choices are remembered."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import gui.main_window as mw  # noqa: E402
from tests.test_batch_operations_and_undo import _app, window  # noqa: E402,F401


def test_every_lookup_is_in_the_right_click_menu(window, monkeypatch):
    seen = {}

    def fake_show(win, table, pos, get_selected_items, get_path, extra_items=None):
        seen["items"] = extra_items([])

    monkeypatch.setattr(mw, "show_table_context_menu", fake_show)
    window._show_table_context_menu(None)
    sub = next(i for i in seen["items"] if isinstance(i, mw.Submenu))
    assert sub.text == "Look Up"
    assert [i.text for i in sub.items] == [
        "TMDB (&Movie)…", "TMDB (&TV Show)…", "TheTVDB (T&V Show)…", "&Subtitles (OpenSubtitles)…",
    ]


def test_zero_pad_choice_is_saved(monkeypatch):
    saved = {}
    monkeypatch.setattr(mw, "set_setting", lambda section, key, value: saved.update({(section, key): value}))
    mw._remember_zero_pad(True, 3)
    assert saved == {("rename", "zero_pad"): "1", ("rename", "zero_pad_width"): "3"}
