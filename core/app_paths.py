"""
core/app_paths.py

Where this app's persistent files live (settings ini, crash log, bundled
tools/), via redactor_common.core.app_paths -- this project's own root
is passed in, since the shared package lives in site-packages and can't
find it from its own location. Replaces core/config.py's private
_app_dir() and core/external_tools.py's _bundled_tools_dir() walk.
"""

from __future__ import annotations

from pathlib import Path

from redactor_common.core import app_paths as _shared

APP_SLUG = "videoredactor"
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def base_dir() -> Path:
    return _shared.base_dir(PROJECT_ROOT)


def tools_dir() -> Path:
    return _shared.tools_dir(PROJECT_ROOT)


def asset_path(relative: str) -> Path:
    return _shared.asset_path(relative, PROJECT_ROOT)


def settings_ini_path() -> Path:
    return _shared.settings_ini_path(APP_SLUG, PROJECT_ROOT)


def crash_log_path() -> Path:
    return _shared.crash_log_path(APP_SLUG, PROJECT_ROOT)
