"""
Export/Import Settings adapter: videoredactor's settings.ini as the
redactor_common settings bundle sees it (File > Export Settings / Import
Settings; the format and the secret guard live in
redactor_common.core.settings_bundle).

The ini stores everything as strings, so every key has an explicit codec:
`read` turns the stored text into a JSON value (falling back to the default
when it is missing or hand-edited into garbage) and `coerce` validates an
imported JSON value and turns it back into ini text. Only keys listed here
can ever be read or written -- an imported file can't touch anything else.

SECRETS ARE NOT HERE, on purpose. The TMDB / TheTVDB / OpenSubtitles keys
live in the credential store (core/api_keys.py) and the `[secrets]
allow_unencrypted_fallback` consent flag stays per computer; neither has a
key in any section below, and the bundle layer drops secret-looking names
as a second line of defence.

Sections the window must refresh live after an import are named in
REFRESH_COLUMNS / REFRESH_PANEL (gui/main_window.py reads them).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Callable

from redactor_common.core import settings_bundle as sb
from redactor_common.core import settings_coerce as sc
from redactor_common.core.settings_coerce import required
from redactor_common.core.pipeline import Recipe

from core import config
from core.controlled_vocab import DEFAULT_GENRE_OPTIONS, DEFAULT_LANGUAGE_OPTIONS
from core.external_tools import KNOWN_EXECUTABLES
from core.filename_pattern import MAX_PATTERN_HISTORY
from core.imdb_import import BuildOptions
from core.redact_steps import recipe_to_setting
from core.table_settings import sanitize_hidden_fields
from core.transcode_settings import DEFAULT_AUDIO_BITRATE, DEFAULT_CRF, DEFAULT_THREADS
from core.version import APP_VERSION
from core.video_duplicates import HAMMING_THRESHOLD

APP_SLUG = "videoredactor"

# The list separator controlled_vocab and filename_pattern store their lists
# with (a unit separator, since a pattern or a genre may contain commas).
_UNIT_SEPARATOR = "\x1f"
_MAX_TEXT = 2048

REFRESH_COLUMNS = {"columns"}
REFRESH_PANEL = {"columns", "vocabulary"}


# --- codecs --------------------------------------------------------------------


# The validators are redactor_common.core.settings_coerce's (total: garbage in gives None); required() turns a
# None into this adapter's ValueError, whose message names the rejected value in the import report.
_TEXT_ERROR = "expected a single-line text value"
_BOOL_ERROR = "expected true or false"


def _single_line(value: Any, check: Callable[[str], bool] | None = None) -> str:
    return required(sc.coerce_str(value, single_line=True, max_len=_MAX_TEXT, check=check), _TEXT_ERROR)


def _str_codec(default: str = "", check: Callable[[str], bool] | None = None):
    def read(raw: str) -> str:
        return raw if raw else default

    def coerce(value: Any) -> str:
        return _single_line(value, check)

    return read, coerce


def _bool_codec(default: bool):
    def read(raw: str) -> bool:
        return default if raw == "" else raw == "1"  # the app itself reads these as == "1"

    def coerce(value: Any) -> str:
        return "1" if required(sc.coerce_bool(value), _BOOL_ERROR) else "0"

    return read, coerce


def _int_codec(default: int, low: int, high: int):
    def read(raw: str) -> int:
        return sc.parse_int(raw, default, low, high)

    def coerce(value: Any) -> str:
        return str(required(sc.coerce_int(value, low, high), f"expected a whole number from {low} to {high}"))

    return read, coerce


def _clean_list(value: Any, *, forbidden: str, limit: int | None = None) -> list[str]:
    return required(sc.coerce_str_list(
        value, strip=forbidden == _UNIT_SEPARATOR, drop_blank=True, forbidden=forbidden,
        single_line=True, max_len=_MAX_TEXT, dedupe=True, limit=limit or None,
    ), "expected a list of text values")


def _json_object_read(raw: str) -> Any:
    """A JSON object kept as one line of ini text; "" when unset or unreadable."""
    data = sc.parse_json(raw)
    return data if isinstance(data, dict) else ""


def _joined_list_codec(separator: str, defaults: list[str], *, limit: int | None = None):
    """A list kept as one separator-joined string (comma for column names,
    unit separator for patterns and vocabularies)."""
    def read(raw: str) -> list[str]:
        if not raw:
            return list(defaults)
        items = [part for part in raw.split(separator) if part]
        return items[:limit] if limit else items

    def coerce(value: Any) -> str:
        return separator.join(_clean_list(value, forbidden=separator, limit=limit))

    return read, coerce


def _hidden_columns_codec():
    def read(raw: str) -> list[str]:
        return sorted(sanitize_hidden_fields({f for f in raw.split(",") if f})) if raw else []

    def coerce(value: Any) -> str:
        names = _clean_list(value, forbidden=",")
        return ",".join(sorted(sanitize_hidden_fields(set(names))))  # keeps the Filename column

    return read, coerce


def _column_widths_codec():
    def read(raw: str) -> dict[str, int]:
        return sc.parse_width_map(raw, lo=1, hi=5000, numeric_strings=False)

    def coerce(value: Any) -> str:
        return json.dumps(required(sc.coerce_width_map(value, lo=1, hi=5000),
                                   "expected a mapping of column name to width"))

    return read, coerce


def _recipe_codec():
    """The Redact recipe as a real JSON object in the bundle (the ini holds
    it as one line of JSON text). Unset = "" (the defaults apply)."""
    read = _json_object_read

    def coerce(value: Any) -> str:
        if value == "":
            return ""  # cleared
        if not isinstance(value, dict):
            raise ValueError("expected a recipe object")
        recipe = Recipe.from_dict(value)
        if not (recipe.order or recipe.enabled or recipe.options):
            raise ValueError("the recipe is empty or unreadable")
        return recipe_to_setting(recipe)  # normalised: unknown fields drop out

    return read, coerce


def _existing_file_codec():
    """A path to a file on THIS computer: an imported path only counts when
    the file is there; otherwise (or when empty) the current value is kept."""
    def read(raw: str) -> str:
        return raw

    def coerce(value: Any) -> str | None:
        value = _single_line(value)
        return value if value and os.path.isfile(value) else None

    return read, coerce


def _imdb_options_codec():
    """The IMDb build options as a real JSON object (unset = defaults)."""
    read = _json_object_read

    def coerce(value: Any) -> str:
        if value == "":
            return ""
        if not isinstance(value, dict):
            raise ValueError("expected an options object")
        return BuildOptions.from_json(json.dumps(value)).to_json()  # normalised; bad parts fall back

    return read, coerce


def _history_codec():
    """[history] enabled, read the way core/history.history_enabled() reads it ("true"/"1"/... = on, anything
    else or missing = off) and written the way set_history_enabled() writes it."""
    def read(raw: str) -> bool:
        return sc.parse_bool(raw, False, other_is_false=True)

    def coerce(value: Any) -> str:
        return "true" if required(sc.coerce_bool(value), _BOOL_ERROR) else "false"

    return read, coerce


def _bitrate_ok(text: str) -> bool:
    return re.fullmatch(r"\d{1,4}[kKmM]?", text) is not None


@dataclass(frozen=True)
class Key:
    name: str  # key in the bundle
    ini_section: str
    ini_key: str
    read: Callable[[str], Any]
    coerce: Callable[[Any], "str | None"]


def _key(name: str, ini_section: str, ini_key: str, codec) -> Key:
    return Key(name, ini_section, ini_key, *codec)


_TOOL_KEYS = [_key(exe, "tools", exe, _str_codec()) for exe in sorted(KNOWN_EXECUTABLES)]

# section key -> (label, portable, keys)
SECTIONS: dict[str, tuple[str, bool, list[Key]]] = {
    "redact": ("Redact recipe", True, [
        _key("recipe", "redact", "recipe", _recipe_codec()),
    ]),
    "patterns": ("Rename, move and parse patterns", True, [
        _key("history", "filename_patterns", "history",
             _joined_list_codec(_UNIT_SEPARATOR, [], limit=MAX_PATTERN_HISTORY)),
        _key("move_pattern", "rename", "move_pattern", _str_codec()),
    ]),
    "columns": ("Table columns (order, visibility, widths)", True, [
        _key("order", "table", "column_order", _joined_list_codec(",", [])),
        _key("hidden", "table", "hidden_columns", _hidden_columns_codec()),
        _key("widths", "table", "column_widths", _column_widths_codec()),
    ]),
    "defaults": ("Field defaults (zero-padding, ASCII names)", True, [
        _key("zero_pad", "rename", "zero_pad", _bool_codec(False)),
        _key("zero_pad_width", "rename", "zero_pad_width", _int_codec(2, 1, 9)),
        _key("ascii_only", "rename", "ascii_only", _bool_codec(False)),
        _key("auto_number_padding", "auto_numbering", "padding", _int_codec(2, 1, 9)),
    ]),
    "vocabulary": ("Genre and language lists", True, [
        _key("genres", "vocabulary", "genres", _joined_list_codec(_UNIT_SEPARATOR, DEFAULT_GENRE_OPTIONS)),
        _key("languages", "vocabulary", "languages",
             _joined_list_codec(_UNIT_SEPARATOR, DEFAULT_LANGUAGE_OPTIONS)),
    ]),
    "media": ("Conversion and duplicate-detection preferences", True, [
        _key("crf", "transcode", "crf", _int_codec(DEFAULT_CRF, 0, 51)),
        _key("audio_bitrate", "transcode", "audio_bitrate", _str_codec(DEFAULT_AUDIO_BITRATE, _bitrate_ok)),
        _key("threads", "transcode", "threads", _int_codec(DEFAULT_THREADS, 0, 256)),
        _key("duplicate_threshold", "duplicates", "threshold", _int_codec(HAMMING_THRESHOLD, 0, 32)),
    ]),
    # Opt-in recording: a bundle exported with history ON turns it on here when imported (the label says so,
    # it is what the import dialog shows); a bundle without this key leaves the current value alone.
    "history": ("Tools > History on/off (importing a bundle with it on turns recording on)", True, [
        _key("enabled", "history", "enabled", _history_codec()),
    ]),
    "imdb_options": ("IMDb database build options", True, [
        _key("options", "imdb", "options", _imdb_options_codec()),
    ]),
    "tools": ("External tool paths", False, _TOOL_KEYS),
    "imdb": ("IMDb database and dataset file paths", False, [
        _key("database", "imdb", "database", _existing_file_codec()),
        _key("basics_file", "imdb", "basics_file", _existing_file_codec()),
        _key("ratings_file", "imdb", "ratings_file", _existing_file_codec()),
        _key("episodes_file", "imdb", "episodes_file", _existing_file_codec()),
        _key("akas_file", "imdb", "akas_file", _existing_file_codec()),
    ]),
    "folders": ("Last-used folders and library root", False, [
        _key("last_folder", "general", "last_folder", _str_codec()),
        _key("last_files_folder", "general", "last_files_folder", _str_codec()),
        _key("library_root", "rename", "library_root", _str_codec()),
    ]),
}


class VideoSettingsAdapter(sb.SettingsAdapter):
    app_slug = APP_SLUG
    app_version = APP_VERSION

    def __init__(self, redetect_tools: Callable[[], None] | None = None) -> None:
        self.redetect_tools = redetect_tools

    def sections(self) -> list[sb.SectionSpec]:
        return [sb.SectionSpec(key, label, portable) for key, (label, portable, _) in SECTIONS.items()]

    def read_section(self, key: str) -> dict[str, Any]:
        _label, _portable, keys = SECTIONS[key]
        parser = config.load_config()
        return {k.name: k.read(parser.get(k.ini_section, k.ini_key, fallback="")) for k in keys}

    def write_section(self, key: str, values: dict[str, Any]) -> None:
        """Validates every value first; valid ones are written in ONE atomic
        save, then a ValueError names the rejected ones (so the section is
        reported as partly applied rather than silently ignored)."""
        _label, _portable, keys = SECTIONS[key]
        known = {k.name: k for k in keys}
        parser = config.load_config()
        rejected: list[str] = []
        for name, value in values.items():
            spec = known.get(name)
            if spec is None:
                continue  # unknown key: ignored
            try:
                text = spec.coerce(value)
            except ValueError as exc:
                rejected.append(f"{name} ({exc})")
                continue
            if text is None:
                continue  # not applicable here (e.g. a file that isn't on this computer): keep the current value
            if text == "":
                if parser.has_section(spec.ini_section):
                    parser.remove_option(spec.ini_section, spec.ini_key)
                continue
            if not parser.has_section(spec.ini_section):
                parser.add_section(spec.ini_section)
            parser.set(spec.ini_section, spec.ini_key, text)
        config.save_config(parser)
        if rejected:
            raise ValueError("ignored invalid value(s): " + ", ".join(rejected))
