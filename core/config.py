"""
App configuration: a single settings.ini next to the executable.

Same reasoning as the epub tool's v31 fix: Windows Registry settings
don't reliably survive version upgrades/reinstalls, so this project
starts directly with the ini-next-to-exe approach rather than
rediscovering that the hard way.

Not TMDB-specific -- this is general config plumbing. TMDB's API key is
just the first thing that needs it.
"""

from __future__ import annotations
import configparser
import os
import sys
from pathlib import Path


def _app_dir() -> Path:
    """Directory the ini file lives next to.

    When frozen (e.g. via PyInstaller), sys.executable is the .exe
    itself, so its parent is the right place -- same as the epub tool's
    "ini next to the exe" approach. When running from source (this
    sandbox, or a dev checkout), falls back to the project root.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


CONFIG_PATH = _app_dir() / "settings.ini"


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
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        parser.write(f)


def get_setting(section: str, key: str, default: str = "") -> str:
    parser = load_config()
    return parser.get(section, key, fallback=default)


def set_setting(section: str, key: str, value: str) -> None:
    parser = load_config()
    if not parser.has_section(section):
        parser.add_section(section)
    parser.set(section, key, value)
    save_config(parser)
