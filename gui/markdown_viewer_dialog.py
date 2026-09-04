"""
MarkdownViewerDialog: read-only, scrollable viewer for a bundled
markdown document (CHANGELOG.md today; reusable for ABOUT.md or
anything else later without writing a new dialog class per document).

Uses QTextEdit.setMarkdown() -- Qt's own built-in markdown renderer,
no external dependency needed. Same "in-app changelog viewer" concept
the epub tool has, whose own v37/v48 changelog entries are exactly
about markdown-rendering quirks in that viewer -- worth remembering
this class of bug exists (an HTML-tag-shaped bracket sequence getting
swallowed by markdown's raw-HTML passthrough) even though this
project's own CHANGELOG.md has been kept free of that pattern from the
start rather than hit it reactively.

NOTE: not runnable in this sandbox -- no PyQt6. Syntax-checked and
reviewed only.
"""

from __future__ import annotations
from pathlib import Path
from typing import Optional

from PyQt6.QtWidgets import QDialog, QVBoxLayout, QTextEdit, QPushButton, QLabel


class MarkdownViewerDialog(QDialog):
    def __init__(self, title: str, markdown_path: Path, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(700, 600)

        layout = QVBoxLayout(self)

        text_edit = QTextEdit()
        text_edit.setReadOnly(True)

        try:
            content = markdown_path.read_text(encoding="utf-8")
            text_edit.setMarkdown(content)
        except OSError as e:
            # Missing/unreadable file shouldn't crash the dialog open --
            # show the problem in the viewer itself, same "fail loud in
            # the UI, not silently" principle used throughout this app.
            layout.addWidget(QLabel(f"Could not load {markdown_path.name}: {e}"))

        layout.addWidget(text_edit)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        layout.addWidget(close_button)
