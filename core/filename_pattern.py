"""
Filename <-> metadata pattern engine, direct analog of the epub tool's
Rename/Export by Pattern + reverse Parse Filename->Metadata feature.

Pattern syntax: %field_name% placeholders (e.g. "%show_title% - S%season_number%E%episode_number% - %title%"),
same convention the epub tool uses. Only EDITABLE_FIELDS names are valid
placeholders in both directions -- rendering into a filename and parsing
back out of one -- since those are the only fields that get written back
into a file's tags; the read-only technical fields (resolution, codec,
etc.) are derived FROM the file, not meaningfully re-derivable from a
filename string.

Literal (non-placeholder) text in a pattern is matched with FLEXIBLE
whitespace during parsing -- runs of literal whitespace become `\\s+`
(one-or-more) rather than an exact character match. This is the epub
tool's v38 fix (a filename with one extra space used to fail to parse
at all, since literal whitespace was escaped for an exact match) applied
here from the start rather than rediscovered the same way.
"""

from __future__ import annotations
import re
from typing import Optional

from core.video_metadata import VideoMetadata, ContentType, EDITABLE_FIELDS

PLACEHOLDER_RE = re.compile(r"%(\w+)%")

# Windows illegal filename characters -- NTFS/Windows Explorer reject
# all of these regardless of filesystem, so stripping them applies
# whether the file ends up on Windows or not.
ILLEGAL_FILENAME_CHARS = '<>:"/\\|?*'

RESERVED_WINDOWS_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

MAX_FILENAME_LENGTH = 200  # conservative; Windows' real limit is 255 but leaves room for extension/path


def render_filename(metadata: VideoMetadata, pattern: str) -> str:
    """Fill a pattern's %field% placeholders from `metadata`, producing
    a filesystem-safe filename stem (no extension -- caller appends the
    original extension, matching the epub tool's rename convention of
    always preserving the source extension regardless of what's typed).

    An unrecognized placeholder (not in EDITABLE_FIELDS) is left in the
    output literally rather than silently dropped -- a typo'd
    placeholder should be visibly wrong in the preview, not
    invisibly eaten.
    """
    def replace(match: re.Match) -> str:
        field_name = match.group(1)
        if field_name not in EDITABLE_FIELDS:
            return match.group(0)
        value = getattr(metadata, field_name, None)
        if value is None:
            return ""
        if isinstance(value, ContentType):
            value = value.value
        return str(value)

    raw = PLACEHOLDER_RE.sub(replace, pattern)
    return sanitize_filename_stem(raw)


def sanitize_filename_stem(raw: str) -> str:
    """Strip illegal characters, collapse whitespace runs left behind by
    empty-field substitutions, trim Windows-illegal trailing space/dot,
    and cap length. Falls back to 'untitled' for a stem that sanitizes
    down to nothing (e.g. a pattern that was ONLY an empty placeholder)
    rather than producing an empty filename.
    """
    for ch in ILLEGAL_FILENAME_CHARS:
        raw = raw.replace(ch, "")
    raw = re.sub(r"\s+", " ", raw).strip()
    raw = raw.rstrip(" .")
    if not raw:
        raw = "untitled"
    return raw[:MAX_FILENAME_LENGTH]


def _flexible_literal_regex(text: str) -> str:
    """Escape literal pattern text for regex matching, converting runs
    of whitespace to `\\s+` (one-or-more) rather than an exact-character
    match. Deliberately `\\s+` not `\\s*` (zero-or-more) -- the epub
    tool's own v38 fix used the same distinction: zero-or-more would
    reintroduce field-boundary ambiguity for "%field% %field%"-shaped
    patterns, where the space is what actually separates two captures.
    """
    parts = re.split(r"(\s+)", text)
    escaped = [r"\s+" if part.isspace() else re.escape(part) for part in parts if part]
    return "".join(escaped)


def _build_pattern_regex(pattern: str) -> Optional[re.Pattern]:
    """Compile a %field%-pattern into a regex with one named capture
    group per recognized placeholder. Returns None (not a raised
    exception) if the pattern is unusable -- e.g. the same field name
    appears twice, which Python's re module rejects as a duplicate
    named group -- so the caller can report "this pattern can't be used
    to parse" cleanly rather than crash.
    """
    parts = re.split(r"(%\w+%)", pattern)
    regex_parts = []
    for part in parts:
        m = re.fullmatch(r"%(\w+)%", part)
        if m:
            field_name = m.group(1)
            if field_name in EDITABLE_FIELDS:
                # Non-greedy capture -- a bare %title% would otherwise
                # trivially swallow the entire rest of the string ("the
                # PATTERN's" behavior noted in the epub tool's own v46:
                # this is expected/correct for a single free-floating
                # placeholder, and non-greedy is what lets adjacent
                # literal text still anchor the boundary correctly when
                # there IS surrounding literal text).
                regex_parts.append(f"(?P<{field_name}>.+?)")
            else:
                # Unrecognized placeholder name -- treat as literal text
                # (matches it exactly) rather than silently dropping it
                # from the pattern, so a typo'd field name fails to
                # match obviously instead of matching something wrong.
                regex_parts.append(re.escape(part))
        elif part:
            regex_parts.append(_flexible_literal_regex(part))

    regex_str = "^" + "".join(regex_parts) + "$"
    try:
        return re.compile(regex_str)
    except re.error:
        return None


def parse_filename(stem: str, pattern: str) -> Optional[dict[str, str]]:
    """Extract field values from a filename stem using a %field%
    pattern -- the reverse of render_filename(). Returns None if the
    pattern doesn't compile or the filename doesn't match it (caller
    treats both the same way: "couldn't parse this file with this
    pattern"), or a dict of {field_name: extracted_value} on success.

    Extracted values are stripped of leading/trailing whitespace but
    otherwise passed through as plain strings -- the caller is
    responsible for any further type conversion (e.g. season_number to
    int), since this function has no way to know a placeholder was
    meant to be numeric versus text-that-happens-to-look-numeric.
    """
    compiled = _build_pattern_regex(pattern)
    if compiled is None:
        return None

    match = compiled.match(stem)
    if not match:
        return None

    result = {}
    for field_name, value in match.groupdict().items():
        result[field_name] = value.strip()
    return result


def validate_filename_stem(stem: str) -> Optional[str]:
    """Check a hand-typed filename stem for Windows-filesystem
    validity, returning a specific human-readable reason it's invalid,
    or None if it's fine. Deliberately reports the SPECIFIC problem
    rather than silently sanitizing (unlike render_filename's pattern-
    output path, which sanitizes automatically) -- a hand-typed,
    deliberate rename deserves "here's what's wrong," not a silent
    rewrite of what the user actually typed.
    """
    if not stem or not stem.strip():
        return "Filename cannot be empty"
    if stem != stem.rstrip(" ."):
        return "Filename cannot end with a space or period (Windows restriction)"
    illegal_found = sorted(set(ILLEGAL_FILENAME_CHARS) & set(stem))
    if illegal_found:
        return f"Filename contains illegal character(s): {' '.join(illegal_found)}"
    base_name = stem.split(".")[0].upper()
    if base_name in RESERVED_WINDOWS_NAMES:
        return f"'{base_name}' is a reserved Windows filename"
    if len(stem) > 255:
        return "Filename too long (255 character limit)"
    return None


# --- Shared pattern history (Rename dialog + Parse Filename dialog) ------
# Kept in this module (not gui/) specifically so it's testable without
# PyQt6 -- it's pure config read/write + string logic, no Qt dependency
# at all, and putting it in a PyQt6-importing GUI file would make it
# untestable in any environment lacking PyQt6, same mistake caught and
# fixed during this feature's own development (see CHANGELOG.md).

from core.config import get_setting, set_setting

PATTERN_HISTORY_SECTION = "filename_patterns"
PATTERN_HISTORY_KEY = "history"
MAX_PATTERN_HISTORY = 10

# Unit separator (\x1f), not comma -- a pattern can legitimately contain
# commas itself (e.g. "%title%, %release_date%"), which a comma-joined
# history list would silently corrupt on the next load.
_HISTORY_DELIMITER = "\x1f"


def load_pattern_history() -> list[str]:
    raw = get_setting(PATTERN_HISTORY_SECTION, PATTERN_HISTORY_KEY, "")
    return [p for p in raw.split(_HISTORY_DELIMITER) if p] if raw else []


def save_pattern_to_history(pattern: str) -> None:
    """Push `pattern` to the front of the shared history, deduplicating
    (a re-used pattern moves to the front rather than appearing twice)
    and capping length so settings.ini doesn't grow unbounded.
    """
    history = load_pattern_history()
    history = [p for p in history if p != pattern]
    history.insert(0, pattern)
    history = history[:MAX_PATTERN_HISTORY]
    set_setting(PATTERN_HISTORY_SECTION, PATTERN_HISTORY_KEY, _HISTORY_DELIMITER.join(history))
