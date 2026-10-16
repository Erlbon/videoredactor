"""
gui/lookup.py

Network lookups (TMDB, TheTVDB, OpenSubtitles) off the GUI thread: a slow
or stalled request used to freeze the whole window for its 10-15 s
timeout. run_lookup() runs the call through redactor_common's run_busy()
(a worker thread under the shared busy dialog, which appears once the call
has taken longer than a moment), with the owning window/dialog disabled and
a wait cursor meanwhile -- the local event loop it runs would otherwise let
the user click around (or start a second lookup) before the dialog shows.
Exceptions from the lookup are re-raised here unchanged, so callers keep
their existing `except TMDBError` blocks.
"""

from __future__ import annotations

import threading
from typing import Any, Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QWidget
from redactor_common.gui.progress import run_busy

LOOKUP_LABEL = "Looking up..."  # also the offline IMDb database's searches (TMDBSearchDialog with source=)


def run_lookup(owner: QWidget, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    was_enabled = owner.isEnabled()
    owner.setEnabled(False)
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        return run_busy(owner, LOOKUP_LABEL, fn, *args, **kwargs)
    finally:
        QApplication.restoreOverrideCursor()
        owner.setEnabled(was_enabled)


CANCELLED = object()  # what run_cancellable_lookup() returns when the user pressed Cancel


def run_cancellable_lookup(owner: QWidget, fn: Callable[..., Any], *args: Any, label: str = LOOKUP_LABEL,
                           cancel_event: threading.Event | None = None, **kwargs: Any) -> Any:
    """run_lookup() with a working Cancel button: returns CANCELLED when it was pressed (the request already on its
    way is abandoned and its answer dropped; `cancel_event`, if given, is set so a long read such as a whole-file
    hash stops at its next block). Exceptions from `fn` are re-raised unchanged, like run_lookup()."""
    was_enabled = owner.isEnabled()
    owner.setEnabled(False)
    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        result = run_busy(owner, label, fn, *args, cancellable=True, on_cancel=CANCELLED, **kwargs)
        if result is CANCELLED and cancel_event is not None:
            cancel_event.set()
        return result
    finally:
        QApplication.restoreOverrideCursor()
        owner.setEnabled(was_enabled)
