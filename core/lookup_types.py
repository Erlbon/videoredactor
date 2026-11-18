"""
core/lookup_types.py

Which lookup belongs to which Content Type, and the "Show adult content" switch. Qt-free.

Owner's decisions (2026-11-18, decision page questions 1-3):
  1A  A lookup that does not fit the selected files' type is HIDDEN from Metadata > Look Up and the row
      right-click menu (its shortcut is idle too), the way Get Lyrics already worked.
  2B  The mapping below: each database for the kind of thing it holds, plus two overlaps (IMDb also for Music
      Videos, TMDB Movie also for Porn). Subtitles fits every type.
  3B  A file with no Content Type fits every lookup. A selection of several files shows a lookup only when it
      fits EVERY selected file.

"Show adult content" (Preferences > Adult Content; the owner's pick over a separate Adult Redactor, 2026-11-18):
off hides the Porn content type, ThePornDB and StashDB (menus, API keys, the Redact step) and the Porn Tags
list. When it has never been set, it counts as ON if a ThePornDB or StashDB key is saved, so nobody who already
uses those lookups loses them on upgrade; otherwise OFF.
"""

from __future__ import annotations

from typing import Iterable, Optional

from core import config
from core.video_metadata import ContentType

ADULT_SECTION = "adult"
ADULT_KEY = "show"

_M, _T, _V, _P = ContentType.MOVIE, ContentType.TV, ContentType.MUSIC_VIDEO, ContentType.PORN

# lookup key (without the menu's key prefix) -> the content types it is for; None = every type
LOOKUP_TYPES: dict[str, Optional[frozenset]] = {
    "tmdb_movie": frozenset({_M, _P}),
    "tmdb_tv": frozenset({_T}),
    "tvdb": frozenset({_T}),
    "imdb_local": frozenset({_M, _T, _V}),
    "theporndb": frozenset({_P}),
    "stashdb": frozenset({_P}),
    "subtitles": None,
    "get_lyrics": frozenset({_V}),
    "get_cover": frozenset({_V}),
    "get_poster": frozenset({_M, _T, _P}),
}

# Hidden everywhere while "Show adult content" is off.
ADULT_LOOKUPS = frozenset({"theporndb", "stashdb"})
ADULT_API_KEYS = ("theporndb", "stashdb")


def _adult_key_saved() -> bool:
    from core import stashdb_client, theporndb_client

    try:
        return bool(theporndb_client.get_api_key() or stashdb_client.get_api_key())
    except Exception:  # a credential store that cannot be read: treat as no key
        return False


def adult_default() -> bool:
    """The value "Show adult content" has before it was ever set (see the module note)."""
    return _adult_key_saved()


def adult_shown() -> bool:
    raw = config.get_setting(ADULT_SECTION, ADULT_KEY, "").strip()
    if raw == "":
        return adult_default()
    return raw not in ("0", "false", "False", "no")


def lookup_fits(key: str, types: Iterable[ContentType], adult: bool) -> bool:
    """True when the lookup `key` belongs in the menu for files of these content types (one entry per selected
    file; an empty selection fits everything, as the menu was before)."""
    if key in ADULT_LOOKUPS and not adult:
        return False
    allowed = LOOKUP_TYPES.get(key)
    if allowed is None:
        return True
    return all(t is ContentType.UNSET or t in allowed for t in types)


def content_type_choices(adult: bool, current: Iterable[ContentType] = ()) -> list[ContentType]:
    """The Content Type pick-list: Porn only while adult content is shown, or when a file already is Porn (its
    value is never hidden away)."""
    keep_porn = adult or any(t is ContentType.PORN for t in current)
    return [t for t in ContentType if keep_porn or t is not ContentType.PORN]
