"""
videocli/fields.py

Metadata field names for the command line. Any spelling the user is likely to type finds the field
("genre", "genre_tags", "season", "Season Number", "year"), and a value is checked before anything is written.
"""

from __future__ import annotations

import datetime
import re

from core.video_metadata import EDITABLE_FIELDS, NUMERIC_FIELDS, ContentType
from redactor_common.cli import CliError
from redactor_common.cli.values import check_text, is_ascii_number

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

# Fields that may hold more than one line (a plot summary); everything else is a single line.
MULTILINE_FIELDS = {"description", "synopsis", "comment", "lyrics", "plot", "long_description"}

DEFAULT_INFO_FIELDS = ["content_type", "title", "show_title", "season_number", "episode_number", "release_date"]


def _real_date(value: str) -> bool:
    parts = [int(p) for p in value.split("-")]
    try:
        datetime.date(parts[0], parts[1] if len(parts) > 1 else 1, parts[2] if len(parts) > 2 else 1)
    except ValueError:
        return False
    return True


def resolve_field(name: str) -> str:
    """The metadata field for what the user typed. Raises CliError listing the valid names."""
    key = name.strip().lower().replace(" ", "_").replace("-", "_")
    field = _LOOKUP.get(key) or _LOOKUP.get(key.replace("_", ""))
    if field is None:
        raise CliError(f"unknown field {name!r}. Fields: {', '.join(FIELD_NAMES)}")
    return field


def check_value(field: str, value: str) -> str:
    """The value to store (stripped), or a CliError when the field would not take it. "" clears the field."""
    value = check_text(field, value, multiline=field in MULTILINE_FIELDS)
    if not value:
        return ""
    if field == "content_type":
        match = next((c for c in CONTENT_TYPES if c.lower() == value.lower()), None)
        if match is None:
            raise CliError(f"content_type must be one of: {', '.join(CONTENT_TYPES)}")
        return match
    if field in NUMERIC_FIELDS:
        if not is_ascii_number(value):
            raise CliError(f"{field} must be a whole number, not {value!r}")
        number = int(value)
        if field == "personal_rating" and not 1 <= number <= 5:
            raise CliError("personal_rating must be from 1 to 5")
        if number > 99999:
            raise CliError(f"{field} is too large: {value}")
        return str(number)  # "007" is stored, and compared, as 7
    if field == "release_date":
        if not _DATE.match(value) or not _real_date(value):
            raise CliError(f"release_date must be a real date, YYYY, YYYY-MM or YYYY-MM-DD, not {value!r}")
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
