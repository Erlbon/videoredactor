"""
bump_version.py -- run before each delivery/build.

Reads today's date from the actual system clock and bumps the build
number: if the date changed since the last bump, resets to #01; if
still the same day, increments. Same mechanism as the epub tool's
core bump_version.py (v25) -- built specifically because "remember to
update the date by hand" (v12's approach) silently didn't hold and the
version label went stale.

Usage: python bump_version.py
"""

from __future__ import annotations
import re
from datetime import date
from pathlib import Path

VERSION_FILE = Path(__file__).parent / "core" / "version.py"
VERSION_PATTERN = re.compile(r'APP_VERSION = "(\d{4}-\d{2}-\d{2})#(\d+)"')


def bump_version() -> str:
    content = VERSION_FILE.read_text(encoding="utf-8")
    match = VERSION_PATTERN.search(content)
    if not match:
        raise RuntimeError(f"Could not find APP_VERSION in {VERSION_FILE}")

    old_date_str, old_build_str = match.groups()
    today_str = date.today().isoformat()

    if old_date_str == today_str:
        new_build = int(old_build_str) + 1
    else:
        new_build = 1

    new_version = f"{today_str}#{new_build:02d}"
    new_content = VERSION_PATTERN.sub(f'APP_VERSION = "{new_version}"', content, count=1)
    VERSION_FILE.write_text(new_content, encoding="utf-8")
    return new_version


if __name__ == "__main__":
    new_version = bump_version()
    print(f"Bumped APP_VERSION to {new_version}")
