"""
bump_version.py -- run once per check-in that changes real code (see the
family conventions in CLAUDE.md), instead of hand-editing core/version.py.

Reads today's date from the system clock, never from anyone's memory of
what day it is: same date as the stored APP_VERSION -> the #NN counter
increments; a new day -> resets to #01. The rule itself is
redactor_common.core.version_bump, shared by every Redactor repo (this
file used to carry its own copy -- five near-identical copies across
the family).

Usage:
    python bump_version.py
"""

from pathlib import Path

from redactor_common.core.version_bump import main

VERSION_FILE = Path(__file__).parent / "core" / "version.py"

if __name__ == "__main__":
    main(VERSION_FILE)
