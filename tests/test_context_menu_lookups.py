"""Every lookup in the Metadata > Look Up menu is also in the row right-click menu,
and the zero-pad choices are remembered."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import gui.main_window as mw  # noqa: E402
from tests.test_batch_operations_and_undo import _app, window  # noqa: E402,F401


def _look_up_texts(window, monkeypatch, files):
    seen = {}

    def fake_show(win, table, pos, get_selected_items, get_path, extra_items=None, **_kw):
        seen["items"] = extra_items(files)

    monkeypatch.setattr(mw, "show_table_context_menu", fake_show)
    window._show_table_context_menu(None)
    sub = next(i for i in seen["items"] if isinstance(i, mw.Submenu))
    assert sub.text == "Look Up"
    return [i.text for i in sub.items], seen["items"]


def test_every_lookup_is_in_the_right_click_menu(window, monkeypatch):
    monkeypatch.setattr("core.lookup_types.adult_shown", lambda: True)
    texts, _ = _look_up_texts(window, monkeypatch, [])
    assert texts == [
        "TMDB (&Movie)…", "TMDB (&TV Show)…", "TheTVDB (T&V Show)…", "IMDb (&Local Database)…",
        "ThePornDB…", "StashDB…", "&Subtitles (OpenSubtitles, Lyrics)…",
    ]


def test_adult_content_off_hides_the_scene_lookups(window, monkeypatch):
    monkeypatch.setattr("core.lookup_types.adult_shown", lambda: False)
    texts, _ = _look_up_texts(window, monkeypatch, [])
    assert "ThePornDB…" not in texts and "StashDB…" not in texts
    window._apply_adult_visibility()
    assert not window.actions_by_key["porn_tags"].isVisible()
    assert not window.actions_by_key["lookup_theporndb"].isEnabled()


def test_a_music_video_gets_only_its_own_lookups(window, monkeypatch):
    from types import SimpleNamespace

    from core.video_metadata import ContentType

    monkeypatch.setattr("core.lookup_types.adult_shown", lambda: True)
    mv = SimpleNamespace(metadata=SimpleNamespace(content_type=ContentType.MUSIC_VIDEO), load_error=None,
                         dirty=False, path="song.txt")  # not a video extension: the track-language entry stays greyed
    texts, items = _look_up_texts(window, monkeypatch, [mv])
    assert texts == ["IMDb (&Local Database)…", "&Subtitles (OpenSubtitles, Lyrics)…",
                     "Get L&yrics (LRCLIB)…", "Get &Cover (iTunes, MusicBrainz, Discogs)…"]
    assert window.actions_by_key["get_poster"] not in items


def test_zero_pad_choice_is_saved(monkeypatch):
    saved = {}
    monkeypatch.setattr(mw, "set_setting", lambda section, key, value: saved.update({(section, key): value}))
    mw._remember_zero_pad(True, 3)
    assert saved == {("rename", "zero_pad"): "1", ("rename", "zero_pad_width"): "3"}
