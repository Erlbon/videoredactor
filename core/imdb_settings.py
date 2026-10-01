"""
core/imdb_settings.py

Where the offline IMDb database setup is remembered (settings.ini, via
core/config.py): the built SQLite file, the four dataset files it is built
from, and the build options. Paths are machine-specific (File > Export
Settings keeps them in the unticked "this computer" group); the options
are portable.
"""

from __future__ import annotations

import os

from core.app_paths import base_dir
from core.config import get_setting, set_setting
from core.imdb_import import BuildOptions

SECTION = "imdb"
DATABASE_KEY = "database"
OPTIONS_KEY = "options"
# dataset kind -> ini key; the order is the order of the pickers in the dialog
SOURCE_KEYS = {
    "basics": "basics_file",
    "ratings": "ratings_file",
    "episodes": "episodes_file",
    "akas": "akas_file",
}


def default_database_path() -> str:
    return os.path.join(str(base_dir()), "imdb.db")


def load_database() -> str:
    return get_setting(SECTION, DATABASE_KEY, "").strip()


def save_database(path: str) -> None:
    set_setting(SECTION, DATABASE_KEY, (path or "").strip())


def load_sources() -> dict[str, str]:
    return {kind: get_setting(SECTION, key, "").strip() for kind, key in SOURCE_KEYS.items()}


def save_sources(sources: dict[str, str]) -> None:
    for kind, key in SOURCE_KEYS.items():
        set_setting(SECTION, key, (sources.get(kind) or "").strip())


def load_options() -> BuildOptions:
    return BuildOptions.from_json(get_setting(SECTION, OPTIONS_KEY, ""))


def save_options(options: BuildOptions) -> None:
    set_setting(SECTION, OPTIONS_KEY, options.to_json())


def usable_database() -> str:
    """The configured database path when the file exists, else ""."""
    path = load_database()
    return path if path and os.path.isfile(path) else ""
