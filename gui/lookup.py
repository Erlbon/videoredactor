"""
gui/lookup.py

Network lookups (TMDB, TheTVDB, OpenSubtitles) off the GUI thread: a slow
or stalled request used to freeze the whole window for its 10-15 s
timeout. run_lookup() is redactor_common's call_in_background() with the
owning window/dialog disabled and a wait cursor meanwhile -- the local
event loop it runs would otherwise let the user click around (or start a
second lookup) while the first is in flight. Exceptions from the lookup
are re-raised here unchanged, so callers keep their existing
`except TMDBError` blocks.
"""

from __future__ import annotations

from typing import Any, Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QWidget
from redactor_common.gui.background_call import call_in_background


def run_lookup(owner: QWidget, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    was_enabled = owner.isEnabled()
    owner.setEnabled(False)
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        return call_in_background(fn, *args, **kwargs)
    finally:
        QApplication.restoreOverrideCursor()
        owner.setEnabled(was_enabled)
