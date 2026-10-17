"""
gui/lookup_file_header.py

The header every lookup dialog and queue step shows, so you always know WHICH FILE you are approving:

    File 3 of 12                                  [Skip This File] [Stop Here]    (counter and buttons: batches only)
    The.Office.US.S02E04.Halloween.1080p.mkv      (bold, selectable; cut in the middle when very long, the full path
                                                   is the tooltip)
    D:\\Shows\\The Office  |  22:14  |  1920x1080  |  1.2 GB
    Searching for: The Office (2005) S02E04       (what Parse Release Name made of the file name)
    This file already has TMDB movie:603          (when the file carries a source id)

A FileContext (the file, its place in the queue, what is searched for) is passed to a dialog as `context=`; the dialog
calls add_file_header(dialog, layout, context). In a batch ("File 3 of 12") the header has "Skip This File" (the
dialog closes as cancelled, the loop goes on with the next file, this one untouched) and "Stop Here" (sets
`dialog.stop_requested`, closes it, and the loop ends without touching the rest). Cancel, Esc and the window's X
mean Skip, never "apply something".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

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
    stop_clicked = pyqtSignal()

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
        self.stop_button = QPushButton("Stop Here")
        self.stop_button.setToolTip("End the queue here: this file and every file after it stay untouched")
        self.stop_button.setAutoDefault(False)
        self.stop_button.clicked.connect(self.stop_clicked)
        for button in (self.skip_button, self.stop_button):
            button.setVisible(context.is_batch)
            top.addWidget(button)
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
    """Puts the header at the top of `layout` (a QVBoxLayout of `dialog`) and wires Skip / Stop. Also gives the dialog
    `stop_requested` (False until Stop Here). No context: nothing is added."""
    dialog.stop_requested = False
    dialog.file_header = None
    if context is None:
        return None
    header = LookupFileHeader(context, dialog)
    layout.insertWidget(0, header)
    header.skip_clicked.connect(dialog.reject)

    def stop() -> None:
        dialog.stop_requested = True
        dialog.reject()

    header.stop_clicked.connect(stop)
    dialog.file_header = header
    return header
