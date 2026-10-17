"""
Video-specific settings for the shared Rename/Export by Pattern and
Parse Filename -> Metadata tools, plus their shared pattern history.

The pattern engine itself (%field% rendering, parsing, sanitizing) is
redactor_common's core/rename_pattern.py and core/filename_parser.py
since 2026-09-23 -- this project's own copy was the third independent
implementation of the same engine. What stays here is only what's
genuinely video-specific: which fields are placeholders, which of them
are numeric, and how a VideoMetadata turns into the plain values dict
the shared engine works on. Pure logic, no Qt dependency.
"""

from __future__ import annotations

from core.config import get_setting, set_setting
from core.video_metadata import ContentType, EDITABLE_FIELDS, NUMERIC_FIELDS, VideoMetadata
from redactor_common.core import filename_parser, rename_pattern
from redactor_common.core.pattern_history import UNIT_SEPARATOR, decode_history, dedupe_and_trim

DEFAULT_RENAME_PATTERN = "%show_title% - S%season_number%E%episode_number% - %title%"

# Parse Filename options: numeric fields only match digits, so
# "S01E03" splits cleanly, and lose their filename zero-padding.
# Lyrics (a whole song, many lines) never belong in a file name.
PATTERN_FIELDS = [f for f in EDITABLE_FIELDS if f != "lyrics"]
VALID_FIELD_KEYS = frozenset(PATTERN_FIELDS)
PARSE_NUMERIC_FIELDS = frozenset(NUMERIC_FIELDS)
PARSE_STRIP_ZEROS_FIELDS = frozenset({"season_number", "episode_number"})


RETIRED_TRACK_TITLE_TOKEN = "%track_title%"


def modernize_pattern(pattern: str) -> str:
    """An old saved pattern that names the retired %track_title% placeholder, brought up to date: it now means the
    song title, so it becomes %title% -- unless the pattern already has %title%, which is left as it is (the
    retired token then renders as nothing and matches nothing, never an error). Any other pattern is unchanged."""
    if RETIRED_TRACK_TITLE_TOKEN in pattern and "%title%" not in pattern:
        return pattern.replace(RETIRED_TRACK_TITLE_TOKEN, "%title%")
    return pattern


MUSIC_VIDEO_PARSE_PATTERN = "%artist% - %title%"


def default_parse_pattern(files, fallback: str = DEFAULT_RENAME_PATTERN) -> str:
    """The pattern Parse Filename starts with: "Artist - Title" (artist + the song title) when every file is a
    Music Video, else `fallback` (the TV pattern)."""
    files = list(files)
    if files and all(getattr(f.metadata, "content_type", None) is ContentType.MUSIC_VIDEO for f in files):
        return MUSIC_VIDEO_PARSE_PATTERN
    return fallback


def field_text(metadata: VideoMetadata, field_name: str) -> str:
    """A metadata field as plain text (None -> "", ContentType -> its
    label, int -> digits)."""
    value = getattr(metadata, field_name, None)
    if value is None:
        return ""
    if isinstance(value, ContentType):
        return value.value
    return str(value)


def set_field_text(metadata: VideoMetadata, field_name: str, text: str) -> bool:
    """Stores a string (from a shared dialog or a filename parse) back
    into its typed field. Returns False (and leaves the field untouched)
    for a value that doesn't fit: a non-number for an int field, or an
    unknown Content Type."""
    text = (text or "").strip()
    if field_name in NUMERIC_FIELDS:
        if not text:
            setattr(metadata, field_name, None)
            return True
        try:
            setattr(metadata, field_name, int(float(text)))
        except ValueError:
            return False
        return True
    if field_name == "content_type":
        try:
            metadata.content_type = ContentType(text)
        except ValueError:
            return False
        return True
    setattr(metadata, field_name, text)
    return True


def placeholder_values(metadata: VideoMetadata) -> dict[str, str]:
    return {field: field_text(metadata, field) for field in PATTERN_FIELDS}


def render_filename(metadata: VideoMetadata, pattern: str) -> str:
    """Convenience wrapper over the shared engine, for tests and scripts."""
    return rename_pattern.render_filename(placeholder_values(metadata), modernize_pattern(pattern))


def parse_filename(stem: str, pattern: str) -> dict[str, str] | None:
    """Convenience wrapper over the shared engine with this project's
    field options."""
    return filename_parser.parse_filename(
        stem, modernize_pattern(pattern), set(VALID_FIELD_KEYS), set(PARSE_NUMERIC_FIELDS),
        strip_leading_zeros_fields=set(PARSE_STRIP_ZEROS_FIELDS),
    )


# --- Shared pattern history (Rename dialog + Parse Filename dialog) ------
# Stored \x1f-joined (unit separator, not comma -- a pattern can contain
# commas, e.g. "%title%, %release_date%") in settings.ini, the format this
# project has always used, so older and newer versions read each other's
# history. The dedupe/trim rule is redactor_common's.

PATTERN_HISTORY_SECTION = "filename_patterns"
PATTERN_HISTORY_KEY = "history"
MAX_PATTERN_HISTORY = 10


def load_pattern_history() -> list[str]:
    return dedupe_modernized(decode_history(get_setting(PATTERN_HISTORY_SECTION, PATTERN_HISTORY_KEY, "")))


def dedupe_modernized(patterns: list[str]) -> list[str]:
    """History entries brought up to date (modernize_pattern), a pattern that became a repeat dropped."""
    result: list[str] = []
    for pattern in patterns:
        pattern = modernize_pattern(pattern)
        if pattern not in result:
            result.append(pattern)
    return result


def save_pattern_to_history(pattern: str) -> None:
    """Push `pattern` to the front of the shared history, deduplicated
    and capped at MAX_PATTERN_HISTORY."""
    history = dedupe_and_trim(load_pattern_history(), pattern, MAX_PATTERN_HISTORY)
    set_setting(PATTERN_HISTORY_SECTION, PATTERN_HISTORY_KEY, UNIT_SEPARATOR.join(history))
