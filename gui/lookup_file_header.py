"""
gui/lookup_file_header.py

The header every lookup dialog and queue step shows, so you always know WHICH FILE you are approving:

    File 3 of 12                                  [Skip This File]                (counter and button: batches only)
    The.Office.US.S02E04.Halloween.1080p.mkv      (bold, selectable; cut in the middle when very long, the full path
                                                   is the tooltip)
    D:\\Shows\\The Office  |  22:14  |  1920x1080  |  1.2 GB
    Searching for: The Office (2005) S02E04       (what Parse Release Name made of the file name)
    This file already has TMDB movie:603          (when the file carries a source id)

A FileContext (the file, its place in the queue, what is searched for) is passed to a dialog as `context=`; the dialog
calls add_file_header(dialog, layout, context). In a batch ("File 3 of 12"):

- "Skip This File" (here in the header, and `add_bottom_skip_button` for the bottom row: [Skip This File] [Use
  Selected] [Cancel]) = `dialog.skip_file()`: the dialog closes as rejected, this file stays untouched and the loop
  goes on with the next file;
- Cancel, Esc and the window's X = `dialog.reject()` = cancel the WHOLE lookup (owner, 2026-10-17): it sets
  `dialog.stop_requested`, so this file and every file after it stay untouched while the files confirmed earlier in
  the run keep their staged results. The loops test `stop_requested` after exec().

In a single-file lookup there is no Skip button and Cancel simply closes the dialog. Neither ever applies anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from core.format_helpers import format_duration, format_file_size
from core.release_name_parser import parse_release_name
from core.source_tags import sources_summary


@dataclass
class FileContext:
    """Which file a dialog is about: `index` is 1-based of `total` in a batch (None for a single file)."""

    file: object  # a VideoFile
    index: Optional[int] = None
    total: Optional[int] = None
    searching_for: str = ""

    @property
    def is_batch(self) -> bool:
        return bool(self.total and self.total > 1)


def searching_for_text(stem: str) -> str:
    """'The Office (2005) S02E04' from what Parse Release Name makes of a file name."""
    guess = parse_release_name(stem)
    parts = [guess.title or ""]
    if guess.year:
        parts.append(f"({guess.year})")
    if guess.season is not None and guess.episode is not None:
        parts.append(f"S{guess.season:02d}E{guess.episode:02d}")
    elif guess.season is not None:
        parts.append(f"S{guess.season:02d}")
    return " ".join(p for p in parts if p).strip()


def file_context(vf, index: Optional[int] = None, total: Optional[int] = None) -> FileContext:
    return FileContext(vf, index, total, searching_for_text(vf.path.stem))


def details_line(vf) -> str:
    """Folder, duration, resolution and size on one line (the parts the file has)."""
    md = vf.metadata
    parts = [str(vf.path.parent)]
    if md.duration_seconds:
        parts.append(format_duration(md.duration_seconds))
    if md.resolution:
        parts.append(md.resolution)
    try:
        size = vf.size_bytes
    except OSError:
        size = 0
    if size:
        parts.append(format_file_size(size))
    return "  |  ".join(parts)


class _ElidedLabel(QLabel):
    """A bold, mouse-selectable label that cuts the middle out of a text that does not fit; the full text is the tooltip."""

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self._full = text
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.setMinimumWidth(120)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)  # may shrink: the text is cut
        self.setStyleSheet("font-weight: bold;")
        self._elide()

    def full_text(self) -> str:
        return self._full

    def _elide(self) -> None:
        metrics = QFontMetrics(self.font())
        self.setText(metrics.elidedText(self._full, Qt.TextElideMode.ElideMiddle, max(self.width(), 120)))

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt)
        super().resizeEvent(event)
        self._elide()


class LookupFileHeader(QWidget):
    skip_clicked = pyqtSignal()

    def __init__(self, context: FileContext, parent=None):
        super().__init__(parent)
        self.context = context
        vf = context.file
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 6)
        layout.setSpacing(2)

        top = QHBoxLayout()
        self.counter_label = QLabel(f"File {context.index} of {context.total}" if context.is_batch and context.index
                                    else "")
        self.counter_label.setVisible(context.is_batch)
        top.addWidget(self.counter_label)
        top.addStretch()
        self.skip_button = QPushButton("Skip This File")
        self.skip_button.setToolTip("Leave this file completely untouched and go on with the next one")
        self.skip_button.setAutoDefault(False)
        self.skip_button.clicked.connect(self.skip_clicked)
        self.skip_button.setVisible(context.is_batch)
        top.addWidget(self.skip_button)
        layout.addLayout(top)

        self.name_label = _ElidedLabel(vf.path.name)
        self.name_label.setToolTip(str(vf.path))
        layout.addWidget(self.name_label)

        self.details_label = QLabel(details_line(vf))
        self.details_label.setToolTip(str(vf.path))
        self.details_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.details_label.setStyleSheet("color: gray;")
        self.details_label.setWordWrap(True)  # a long folder path wraps instead of widening the dialog
        layout.addWidget(self.details_label)

        self.searching_label = QLabel(f"Searching for: {context.searching_for}" if context.searching_for else "")
        self.searching_label.setVisible(bool(context.searching_for))
        self.searching_label.setWordWrap(True)
        layout.addWidget(self.searching_label)

        held = sources_summary(vf.metadata)
        self.existing_label = QLabel(f"This file already has {held}" if held else "")
        self.existing_label.setVisible(bool(held))
        self.existing_label.setWordWrap(True)
        layout.addWidget(self.existing_label)


def add_file_header(dialog, layout, context: Optional[FileContext]) -> Optional[LookupFileHeader]:
    """Puts the header at the top of `layout` (a QVBoxLayout of `dialog`) and wires Skip and Cancel (see the module
    docstring). Also gives the dialog `stop_requested` (False until the lookup is cancelled) and `skip_file()`.
    Call it BEFORE the dialog's buttons are connected to `self.reject`, so that they get the batch-aware reject.
    No context: no header; the dialog is a plain single-file one."""
    dialog.stop_requested = False
    dialog.file_header = None
    dialog.skip_file = dialog.reject  # replaced below in a batch, where reject() itself means "cancel the lookup"
    if context is None:
        return None
    header = LookupFileHeader(context, dialog)
    layout.insertWidget(0, header)
    header.skip_clicked.connect(lambda: dialog.skip_file())
    if context.is_batch:
        _install_batch_reject(dialog)
    dialog.file_header = header
    return header


def _install_batch_reject(dialog) -> None:
    """In a batch reject() (the Cancel button, Esc, the window's X) cancels the whole lookup, and skip_file() is the
    old behaviour. Qt calls reject() virtually, and PyQt finds an instance attribute for that, so one instance-level
    reject covers all three; a flag tells the two apart, and a re-entry guard keeps the dialog's own reject (which may
    call super().reject(), dispatching back here) from recursing."""
    state = {"busy": False, "skip": False}
    own_reject = type(dialog).reject

    def reject() -> None:
        if state["busy"]:  # re-entered from own_reject's super().reject(): Qt's implementation
            QDialog.reject(dialog)
            return
        state["busy"] = True
        try:
            if not state["skip"]:
                dialog.stop_requested = True
            own_reject(dialog)
        finally:
            state["busy"] = False

    def skip_file() -> None:
        state["skip"] = True
        try:
            dialog.reject()
        finally:
            state["skip"] = False

    dialog.reject = reject
    dialog.skip_file = skip_file


def add_bottom_skip_button(dialog, row, label: str = "Skip This File", index: int = 1):
    """In a batch, adds the bottom row's Skip button to `row` (a QHBoxLayout that starts with a stretch), left of
    the other buttons: [Skip This File] [Use Selected] [Cancel]. Returns it (None outside a batch)."""
    header = getattr(dialog, "file_header", None)
    if header is None or not header.context.is_batch:
        return None
    button = QPushButton(label)
    button.setToolTip("Leave this file completely untouched and go on with the next one")
    button.setAutoDefault(False)
    button.clicked.connect(lambda: dialog.skip_file())
    row.insertWidget(index, button)
    dialog.skip_button = button
    return button
