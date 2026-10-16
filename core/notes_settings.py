"""
core/notes_settings.py

What Tools > Create Notes remembers (the settings file next to the exe) and where its lookup cache lives. Small on
purpose: the window, the command line and the tests all go through these functions.

  [notes] last_folder    the folder the person used last time
  [notes] kinds          'series,movie' (which folders get a note)
  [notes] cache          '1' (default) / '0': keep answers of earlier lookups on disk
  [notes] dialog_size    'WxH' of the review page
  [notes] wikipedia      '1' (default) / '0': a Wikipedia introduction in the notes
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from core import config
from core.notes_local import ALL_KINDS
from redactor_common.core import notes_cache as shared_cache

SECTION = "notes"
CACHE_FILE_NAME = "videoredactor_notes_cache.sqlite"
_FALSE = frozenset({"0", "false", "no", "off"})


def _get(key: str, default: str = "") -> str:
    try:
        return config.get_setting(SECTION, key, default)
    except Exception:  # noqa: BLE001 -- an unreadable settings file means the defaults
        return default


def _flag(key: str, default: bool = True) -> bool:
    value = _get(key, "1" if default else "0").strip().lower()
    return default if not value else value not in _FALSE


def last_folder() -> str:
    return _get("last_folder")


def set_last_folder(folder: str) -> None:
    config.set_setting(SECTION, "last_folder", folder)


def parse_kinds(text) -> tuple[str, ...]:
    """The kinds named in `text` ('series', 'movie', 'both', a comma list, or a list), in the order series, movie; both when
    the text is empty or unreadable."""
    words = [str(w) for w in text] if isinstance(text, (list, tuple, set, frozenset)) else str(text or "").replace(";", ",").split(",")
    found = set()
    for word in (w.strip().lower() for w in words):
        if word in ALL_KINDS:
            found.add(word)
        elif word in ("both", "all"):
            found.update(ALL_KINDS)
        elif word in ("film", "films", "movies"):
            found.add("movie")
    return tuple(k for k in ALL_KINDS if k in found) or ALL_KINDS


def kinds_text(kinds) -> str:
    return ",".join(parse_kinds(kinds))


def kinds() -> tuple[str, ...]:
    return parse_kinds(_get("kinds"))


def set_kinds(kinds_value) -> None:
    config.set_setting(SECTION, "kinds", kinds_text(kinds_value))


def cache_enabled() -> bool:
    return _flag("cache")


def set_cache_enabled(enabled: bool) -> None:
    config.set_setting(SECTION, "cache", "1" if enabled else "0")


def wikipedia_enabled() -> bool:
    return _flag("wikipedia")


def set_wikipedia_enabled(enabled: bool) -> None:
    config.set_setting(SECTION, "wikipedia", "1" if enabled else "0")


def dialog_size() -> str:
    return _get("dialog_size")


def set_dialog_size(text: str) -> None:
    config.set_setting(SECTION, "dialog_size", text)


# ---- the cache -------------------------------------------------------------------------------------------------------

def cache_path() -> Path:
    """Next to the settings file (never in a library folder)."""
    return Path(config.CONFIG_PATH).parent / CACHE_FILE_NAME


def default_cache(enabled: Optional[bool] = None):
    """The process-wide lookup cache, or None when the setting (or `enabled`) is off."""
    return shared_cache.default_cache(cache_path(), cache_enabled() if enabled is None else enabled)


def clear_cache() -> int:
    """Preferences: the entries removed (0 when there is none)."""
    return shared_cache.clear_default_cache(cache_path())
