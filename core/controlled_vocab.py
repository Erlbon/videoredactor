"""
Controlled vocabulary for Genre and Language fields, replacing freeform
typing with a fixed pick-list (multi-select, since a file can genuinely
have more than one genre, or more than one audio language track).

Storage stays a single comma-separated string in VideoMetadata (same
shape genre_tags already used for freeform text) -- these functions
just parse/serialize between that string and a set of selected values,
so the underlying data model and both backends (mp4/mkv) don't need to
change at all.

The genre/language OPTION LISTS themselves are now user-editable and
persisted (settings.ini via core/config.py), not fixed Python constants
-- matching the epub tool's own "Add/Remove Genres...")/"Add/Remove
Languages..." settings pattern. DEFAULT_GENRE_OPTIONS/
DEFAULT_LANGUAGE_OPTIONS below are only the SEED values used the first
time this runs (nothing persisted yet); every subsequent call reads
the user's actual current list.
"""

from __future__ import annotations

from core.config import get_setting, set_setting

VOCAB_SECTION = "vocabulary"
GENRES_KEY = "genres"
LANGUAGES_KEY = "languages"

# Unit separator, not comma -- a genre or language name is very unlikely
# to contain one, but this matches the same safe-delimiter convention
# core/filename_pattern.py's pattern history already established, rather
# than reintroducing the comma-corruption risk that convention exists
# to avoid.
_DELIMITER = "\x1f"

# Movie + TV genre lists merged and deduplicated from TMDB's own genre
# vocabulary (https://developer.themoviedb.org/reference/genre-movie-list
# and .../genre-tv-list) -- chosen deliberately so genres TMDB import
# already writes land on values this picker also recognizes, rather than
# inventing a separate vocabulary that TMDB-imported text wouldn't match.
# SEED values only -- see module docstring.
DEFAULT_GENRE_OPTIONS = [
    "Action", "Action & Adventure", "Adventure", "Animation", "Comedy",
    "Crime", "Documentary", "Drama", "Family", "Fantasy", "History",
    "Horror", "Kids", "Music", "Mystery", "News", "Reality", "Romance",
    "Sci-Fi & Fantasy", "Science Fiction", "Soap", "TV Movie", "Talk",
    "Thriller", "War", "War & Politics", "Western",
]

# Common languages as (display_name) -- ISO 639 codes deliberately not
# used as the stored value here: genre_tags already stores plain
# display text rather than codes, and matching that existing convention
# keeps both fields consistent and human-readable in the table/panel.
# NOT an exhaustive ISO 639-1 list (~180 entries) -- a practical subset
# covering the languages most likely to actually appear in a personal
# media library; the user can now add more via Settings rather than
# needing a code change. SEED values only -- see module docstring.
DEFAULT_LANGUAGE_OPTIONS = [
    "English", "Spanish", "French", "German", "Italian", "Portuguese",
    "Dutch", "Russian", "Japanese", "Korean", "Mandarin", "Cantonese",
    "Hindi", "Arabic", "Turkish", "Polish", "Swedish", "Norwegian",
    "Danish", "Finnish", "Greek", "Hebrew", "Thai", "Vietnamese",
    "Indonesian", "Czech", "Hungarian", "Romanian", "Ukrainian",
]


def _get_option_list(key: str, defaults: list[str]) -> list[str]:
    """Shared implementation for get_genre_options()/get_language_options().
    Seeds settings.ini with `defaults` on first call (nothing persisted
    yet) so the very first Add/Remove dialog a user opens shows the
    familiar starting list rather than an empty one, then always reads
    from settings.ini afterward -- the defaults are a one-time seed, not
    something re-applied on every call.
    """
    raw = get_setting(VOCAB_SECTION, key, "")
    if not raw:
        set_setting(VOCAB_SECTION, key, _DELIMITER.join(defaults))
        return list(defaults)
    return [v for v in raw.split(_DELIMITER) if v]


def get_genre_options() -> list[str]:
    return _get_option_list(GENRES_KEY, DEFAULT_GENRE_OPTIONS)


def get_language_options() -> list[str]:
    return _get_option_list(LANGUAGES_KEY, DEFAULT_LANGUAGE_OPTIONS)


def add_genre_option(name: str) -> None:
    """Add `name` to the genre list if it's not already there
    (case-sensitive exact match; "Action" and "action" are treated as
    different entries deliberately -- silently merging near-duplicates
    risks surprising the user more than just letting them manage it,
    same as the epub tool leaves other free-text-adjacent lists alone).
    No-op, not an error, if the name is already present or blank.
    """
    name = name.strip()
    if not name:
        return
    options = get_genre_options()
    if name not in options:
        options.append(name)
        set_setting(VOCAB_SECTION, GENRES_KEY, _DELIMITER.join(options))


def remove_genre_option(name: str) -> None:
    """Remove `name` from the genre list. No-op if it's not present --
    this only affects the PICKER's option list, never any file's
    already-stored genre_tags value (core.controlled_vocab.
    serialize_multi_value already preserves a value that isn't in the
    canonical list rather than dropping it, so removing an option here
    doesn't retroactively erase it from files that already have it).
    """
    options = get_genre_options()
    if name in options:
        options.remove(name)
        set_setting(VOCAB_SECTION, GENRES_KEY, _DELIMITER.join(options))


def add_language_option(name: str) -> None:
    name = name.strip()
    if not name:
        return
    options = get_language_options()
    if name not in options:
        options.append(name)
        set_setting(VOCAB_SECTION, LANGUAGES_KEY, _DELIMITER.join(options))


def remove_language_option(name: str) -> None:
    options = get_language_options()
    if name in options:
        options.remove(name)
        set_setting(VOCAB_SECTION, LANGUAGES_KEY, _DELIMITER.join(options))


def parse_multi_value(stored: str) -> set[str]:
    """'Action, Comedy' -> {'Action', 'Comedy'}. Empty/None-ish input
    gives an empty set, not an error -- an unset field is the normal
    case, not a malformed one.
    """
    if not stored:
        return set()
    return {v.strip() for v in stored.split(",") if v.strip()}


def serialize_multi_value(selected: set[str], canonical_order: list[str]) -> str:
    """{'Comedy', 'Action'} -> 'Action, Comedy' (canonical_order's order,
    not the set's arbitrary iteration order) -- keeps the stored/displayed
    string stable and readable rather than shuffling on every save.
    Any selected value not found in canonical_order (e.g. a value that
    arrived from TMDB import and doesn't match this picker's fixed list,
    or a value that WAS a valid option before the user removed it via
    Settings) is appended at the end rather than silently dropped -- the
    picker is a convenience for common values, not a lossy filter on
    existing data.
    """
    ordered = [v for v in canonical_order if v in selected]
    leftover = sorted(v for v in selected if v not in canonical_order)
    return ", ".join(ordered + leftover)
