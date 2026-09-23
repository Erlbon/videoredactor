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
VALID_FIELD_KEYS = frozenset(EDITABLE_FIELDS)
PARSE_NUMERIC_FIELDS = frozenset(NUMERIC_FIELDS)
PARSE_STRIP_ZEROS_FIELDS = frozenset({"season_number", "episode_number"})


def field_text(metadata: VideoMetadata, field_name: str) -> str:
    """A metadata field as plain text (None -> "", ContentType -> its
    label, int -> digits)."""
    value = getattr(metadata, field_name, None)
    if value is None:
        return ""
    if isinstance(value, ContentType):
        return value.value
    return str(value)


def placeholder_values(metadata: VideoMetadata) -> dict[str, str]:
    return {field: field_text(metadata, field) for field in EDITABLE_FIELDS}


def render_filename(metadata: VideoMetadata, pattern: str) -> str:
    """Convenience wrapper over the shared engine, for tests and scripts."""
    return rename_pattern.render_filename(placeholder_values(metadata), pattern)


def parse_filename(stem: str, pattern: str) -> dict[str, str] | None:
    """Convenience wrapper over the shared engine with this project's
    field options."""
    return filename_parser.parse_filename(
        stem, pattern, set(VALID_FIELD_KEYS), set(PARSE_NUMERIC_FIELDS),
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
    return decode_history(get_setting(PATTERN_HISTORY_SECTION, PATTERN_HISTORY_KEY, ""))


def save_pattern_to_history(pattern: str) -> None:
    """Push `pattern` to the front of the shared history, deduplicated
    and capped at MAX_PATTERN_HISTORY."""
    history = dedupe_and_trim(load_pattern_history(), pattern, MAX_PATTERN_HISTORY)
    set_setting(PATTERN_HISTORY_SECTION, PATTERN_HISTORY_KEY, UNIT_SEPARATOR.join(history))
