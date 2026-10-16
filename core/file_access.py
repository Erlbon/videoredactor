"""
core/file_access.py

Read-only files and scratch copies (2026-10-16 review, finding 3).

shutil.copy2 copies the read-only attribute, so the scratch copy of a read-only video was itself read-only: the edit
of the copy failed, and the cleanup `os.remove` of the scratch was refused and swallowed, leaving a full-size hidden
temp file beside the video. Now:
  * a file that is read-only is refused UP FRONT with a clear message, before anything is copied or written
    (require_writable);
  * a scratch copy is made writable the moment it exists (make_scratch_writable), without touching other permission
    bits;
  * removing a scratch copy retries once after clearing the read-only attribute (remove_scratch), and says whether
    it worked.
"""

from __future__ import annotations

import os
import stat


class ReadOnlyFileError(OSError):
    """The file is read-only. Its message is complete and user-facing (no errno prefix)."""

    def __str__(self) -> str:
        return self.args[0] if self.args else "the file is read-only"


def is_read_only(path: str) -> bool:
    """True for a file that exists and cannot be written (Windows: the Read-only attribute)."""
    return os.path.isfile(path) and not os.access(path, os.W_OK)


def read_only_message(path: str) -> str:
    return (
        f"{os.path.basename(path)} is read-only, so it was not changed. Clear \"Read-only\" in the file's "
        f"Properties (or make it writable) and try again."
    )


def require_writable(path: str) -> None:
    """Raises ReadOnlyFileError when `path` is a read-only file."""
    if is_read_only(path):
        raise ReadOnlyFileError(read_only_message(path))


def make_scratch_writable(path: str) -> None:
    """Adds the write permission to a scratch copy that copy2 made read-only; other permission bits stay as they are."""
    try:
        if not os.access(path, os.W_OK):
            os.chmod(path, stat.S_IMODE(os.stat(path).st_mode) | stat.S_IWRITE)
    except OSError:
        pass


def remove_scratch(path: str) -> bool:
    """Removes a file this app created; a refusal because of the read-only attribute is retried once after clearing
    it. True when the file is gone (or never was there)."""
    for attempt in (0, 1):
        try:
            os.remove(path)
            return True
        except FileNotFoundError:
            return True
        except PermissionError:
            if attempt:
                return False
            make_scratch_writable(path)
        except OSError:
            return False
    return False


def unique_temp_path(path: str) -> str:
    """A hidden, random temp name beside `path` in the family's convention (redactor_common.core.temp_names:
    ".<stem>.redact-<8 hex><ext>"; the real extension stays last so ffmpeg still picks the muxer). Never an existing
    path, and never a name a user's own file would have, unlike the old "<stem>.partial<ext>" / "<stem>.repairing<ext>",
    which on a case-insensitive disk could be a real "Movie.Partial.mp4" that then got deleted or overwritten. The file
    is not created: ffmpeg is run with -n (never overwrite) onto it, so a clash fails instead of destroying anything."""
    import uuid  # noqa: PLC0415

    folder, name = os.path.split(path)
    stem, ext = os.path.splitext(name)
    lead = "" if stem.startswith(".") else "."
    while True:
        candidate = os.path.join(folder, f"{lead}{stem}.redact-{uuid.uuid4().hex[:8]}{ext}")
        if not os.path.lexists(candidate):
            return candidate
