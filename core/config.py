"""
App configuration: a single settings.ini next to the executable.

Same reasoning as the epub tool's v31 fix: Windows Registry settings
don't reliably survive version upgrades/reinstalls, so this project
starts directly with the ini-next-to-exe approach rather than
rediscovering that the hard way.

Not TMDB-specific -- this is general config plumbing. TMDB's API key is
just the first thing that needs it.

Filename is app-prefixed ("videoredactor_settings.ini"), not a bare
"settings.ini" -- mp3redactor independently used that exact same
generic name for its own settings file; both apps write next to their
own exe, so a portable folder containing both exes would have had them
silently reading/writing each other's settings, corrupting whichever
wrote last.
"""

from __future__ import annotations
import configparser
import os
import time
from pathlib import Path

from core.app_paths import settings_ini_path

CONFIG_PATH: Path = settings_ini_path()


def load_config() -> configparser.ConfigParser:
    # interpolation=None is essential, not optional: configparser's
    # default interpolation treats '%' as a special character (for
    # '%(varname)s'-style substitution), and this project's entire
    # filename-pattern syntax is built on '%field%' placeholders. Every
    # pattern this app would ever save to settings.ini contains '%',
    # so without this, saving pattern history crashes outright with
    # "invalid interpolation syntax" -- caught for real during this
    # feature's own development (see CHANGELOG.md), not a hypothetical.
    parser = configparser.ConfigParser(interpolation=None)
    if CONFIG_PATH.exists():
        parser.read(CONFIG_PATH, encoding="utf-8")
    return parser


def save_config(parser: configparser.ConfigParser) -> None:
    # Temp file + os.replace: opening CONFIG_PATH with "w" truncates it
    # first, so a crash (or a full disk) mid-write left an empty or
    # half-written settings file -- API keys, tool paths and pattern
    # history gone.
    tmp = CONFIG_PATH.with_name(CONFIG_PATH.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            parser.write(f)
        # Windows: os.replace can fail for a moment when an antivirus or
        # indexer has the target open; a short retry rides that out.
        for attempt in range(10):
            try:
                os.replace(tmp, CONFIG_PATH)
                break
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(0.05)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def get_setting(section: str, key: str, default: str = "") -> str:
    parser = load_config()
    return parser.get(section, key, fallback=default)


def set_setting(section: str, key: str, value: str) -> None:
    parser = load_config()
    if not parser.has_section(section):
        parser.add_section(section)
    parser.set(section, key, value)
    save_config(parser)
