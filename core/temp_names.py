"""
Recognises the temp/backup files the app itself leaves beside videos, so
a folder scan can ignore them (an app killed mid-repair or mid-Redact
would otherwise show its leftovers as videos in the next scan).

Matching is deliberately narrow -- exact marker positions, never a bare
substring -- so a user's own file such as "My.Partial.Cut.mkv" is still
listed. The patterns mirror the names the app writes:

- ``<stem>.repairing<ext>``   core/file_check.repair_path (Check Files > Repair)
- ``<stem>.partial<ext>``     core/ffmpeg_backend.partial_path (remux/convert)
- ``<stem>.redact-orig<ext>`` / ``<stem>.redact-orig<N><ext>``
                              redactor_common commit_in_place backup
- ``.<stem>.redact-<8 hex><ext>``
                              core/redact_steps scratch copy (hidden)

Matching is case-insensitive on every platform (Windows semantics).
"""

from __future__ import annotations

import re

# The marker is the LAST dot-segment before the extension, so a longer
# user name that merely contains the word ("My.Partial.Cut.mkv") is not hit.
_TEMP_NAME_PATTERNS = (
    re.compile(r"^.+\.(?:repairing|partial)\.[^.]+$", re.IGNORECASE),
    re.compile(r"^.+\.redact-orig\d*\.[^.]+$", re.IGNORECASE),
    re.compile(r"^\..+\.redact-[0-9a-f]{8}\.[^.]+$", re.IGNORECASE),
)


def is_app_temp_name(file_name: str) -> bool:
    """True if `file_name` (a bare name, no folder) is one of the app's own
    temp/backup names listed in the module docstring."""
    return any(p.match(file_name) for p in _TEMP_NAME_PATTERNS)
