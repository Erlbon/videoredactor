"""
videocli/fields.py

Metadata field names for the command line. Any spelling the user is likely to type finds the field
("genre", "genre_tags", "season", "Season Number", "year"), and a value is checked before anything is written.
"""

from __future__ import annotations

import re

from core.video_metadata import EDITABLE_FIELDS, NUMERIC_FIELDS, ContentType
from redactor_common.cli import CliError

FIELD_NAMES = list(EDITABLE_FIELDS)

_ALIASES = {
    "genre": "genre_tags", "genres": "genre_tags", "tags": "genre_tags", "year": "release_date", "date": "release_date",
    "show": "show_title", "series": "show_title", "season": "season_number", "episode": "episode_number",
    "type": "content_type", "rating": "personal_rating", "lang": "language", "sorttitle": "sort_title",
}
_LOOKUP: dict[str, str] = {name: name for name in FIELD_NAMES}
_LOOKUP.update({name.replace("_", ""): name for name in FIELD_NAMES})
_LOOKUP.update(_ALIASES)

_DATE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")
_LANGUAGE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?$")
CONTENT_TYPES = [c.value for c in ContentType if c.value]

DEFAULT_INFO_FIELDS = ["content_type", "title", "show_title", "season_number", "episode_number", "release_date"]


def resolve_field(name: str) -> str:
    """The metadata field for what the user typed. Raises CliError listing the valid names."""
    key = name.strip().lower().replace(" ", "_").replace("-", "_")
    field = _LOOKUP.get(key) or _LOOKUP.get(key.replace("_", ""))
    if field is None:
        raise CliError(f"unknown field {name!r}. Fields: {', '.join(FIELD_NAMES)}")
    return field


def check_value(field: str, value: str) -> str:
    """The value to store (stripped), or a CliError when the field would not take it. "" clears the field."""
    value = value.strip()
    if not value:
        return ""
    if field == "content_type":
        match = next((c for c in CONTENT_TYPES if c.lower() == value.lower()), None)
        if match is None:
            raise CliError(f"content_type must be one of: {', '.join(CONTENT_TYPES)}")
        return match
    if field in NUMERIC_FIELDS:
        if not value.isdigit():
            raise CliError(f"{field} must be a whole number, not {value!r}")
        if field == "personal_rating" and not 1 <= int(value) <= 5:
            raise CliError("personal_rating must be from 1 to 5")
    if field == "release_date" and not _DATE.match(value):
        raise CliError(f"release_date must be YYYY, YYYY-MM or YYYY-MM-DD, not {value!r}")
    if field == "language" and not _LANGUAGE.match(value):
        raise CliError(f"language must be a language code (en, eng, nb, en-GB), not {value!r}")
    return value


def parse_assignment(text: str) -> tuple[str, str]:
    """"show_title=Dune" -> (field, checked value). Splits on the first "="."""
    if "=" not in text:
        raise CliError(f"expected FIELD=VALUE, got {text!r}")
    name, _, value = text.partition("=")
    field = resolve_field(name)
    return field, check_value(field, value)
