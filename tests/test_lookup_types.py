"""Which lookup belongs to which Content Type, and the Show adult content switch (core/lookup_types.py;
owner's decisions 1A, 2B, 3B, 4A of 2026-11-18)."""

from __future__ import annotations

import pytest

from core import lookup_types
from core.lookup_types import content_type_choices, lookup_fits
from core.video_metadata import ContentType as CT


@pytest.mark.parametrize("key, ctype, fits", [
    ("tmdb_movie", CT.MOVIE, True), ("tmdb_movie", CT.PORN, True), ("tmdb_movie", CT.TV, False),
    ("tmdb_movie", CT.MUSIC_VIDEO, False),
    ("tmdb_tv", CT.TV, True), ("tvdb", CT.TV, True), ("tvdb", CT.MUSIC_VIDEO, False),
    ("imdb_local", CT.MUSIC_VIDEO, True), ("imdb_local", CT.PORN, False), ("imdb_local", CT.CLIP, False),
    ("theporndb", CT.PORN, True), ("stashdb", CT.MOVIE, False),
    ("get_poster", CT.PORN, True), ("get_poster", CT.MUSIC_VIDEO, False),
    ("get_lyrics", CT.MUSIC_VIDEO, True), ("get_cover", CT.MOVIE, False),
    ("subtitles", CT.MISC, True), ("subtitles", CT.MUSIC_VIDEO, True),
])
def test_mapping_2b(key, ctype, fits):
    assert lookup_fits(key, [ctype], adult=True) is fits


def test_untyped_file_fits_everything_3b():
    for key in lookup_types.LOOKUP_TYPES:
        assert lookup_fits(key, [CT.UNSET], adult=True)
    assert lookup_fits("tvdb", [], adult=True)  # an empty selection: the menu as before


def test_mixed_selection_needs_every_file_to_fit_3b():
    assert not lookup_fits("tvdb", [CT.TV, CT.MUSIC_VIDEO], adult=True)
    assert lookup_fits("tvdb", [CT.TV, CT.UNSET], adult=True)
    assert lookup_fits("imdb_local", [CT.MOVIE, CT.MUSIC_VIDEO], adult=True)
    assert lookup_fits("subtitles", [CT.PORN, CT.MUSIC_VIDEO], adult=True)


def test_adult_off_hides_scene_lookups_even_for_porn():
    assert not lookup_fits("theporndb", [CT.PORN], adult=False)
    assert not lookup_fits("stashdb", [CT.UNSET], adult=False)
    assert lookup_fits("tmdb_movie", [CT.PORN], adult=False)


def test_content_type_choices_keep_an_existing_porn_value():
    assert CT.PORN not in content_type_choices(adult=False)
    assert CT.PORN in content_type_choices(adult=False, current=[CT.PORN])
    assert CT.PORN in content_type_choices(adult=True)


def test_adult_shown_reads_setting_then_falls_back_to_saved_keys(monkeypatch):
    stored = {}
    monkeypatch.setattr(lookup_types.config, "get_setting", lambda s, k, d="": stored.get((s, k), d))
    monkeypatch.setattr(lookup_types, "_adult_key_saved", lambda: True)
    assert lookup_types.adult_shown() is True  # never set + a key saved: on, so nobody loses the lookups
    monkeypatch.setattr(lookup_types, "_adult_key_saved", lambda: False)
    assert lookup_types.adult_shown() is False
    stored[("adult", "show")] = "1"
    assert lookup_types.adult_shown() is True
    stored[("adult", "show")] = "0"
    monkeypatch.setattr(lookup_types, "_adult_key_saved", lambda: True)
    assert lookup_types.adult_shown() is False
