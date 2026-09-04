"""
ApiKeysDialog: "Add External APIs..." -- lets the user enter/edit the
TMDB, TheTVDB, and OpenSubtitles API keys directly in the app, rather
than needing to set environment variables or hand-edit settings.ini.

core.tmdb_client.get_api_key(), core.tvdb_client.get_api_key(), and
core.opensubtitles_client.get_api_key() all check their respective
environment variable FIRST, falling back to settings.ini's stored
value only if no env var is set -- surfaced explicitly in this dialog
when applicable, so a key entered here that doesn't seem to take
effect isn't a silent mystery.

NOTE: not runnable in this sandbox -- no PyQt6. Syntax-checked and
reviewed only.
"""

from __future__ import annotations
import os

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QHBoxLayout, QLineEdit, QLabel, QPushButton,
)

from core.config import get_setting, set_setting


class ApiKeysDialog(QDialog):
    """Usage: dialog = ApiKeysDialog(parent=self); dialog.exec() --
    saves directly on click (Save button), no further caller action
    needed; nothing needs to refresh elsewhere in response, since each
    key is only read at the moment an import is actually attempted.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add External APIs")
        self.resize(440, 280)

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Enter your own API keys for TMDB, TheTVDB, and OpenSubtitles "
            "import. All three are free to obtain from their respective websites."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()

        self.tmdb_edit = QLineEdit(get_setting("tmdb", "api_key", ""))
        form.addRow("TMDB API Key:", self.tmdb_edit)
        if os.environ.get("TMDB_API_KEY"):
            form.addRow("", self._env_var_note("TMDB_API_KEY"))

        self.tvdb_edit = QLineEdit(get_setting("tvdb", "api_key", ""))
        form.addRow("TheTVDB API Key:", self.tvdb_edit)
        if os.environ.get("TVDB_API_KEY"):
            form.addRow("", self._env_var_note("TVDB_API_KEY"))

        self.opensubtitles_edit = QLineEdit(get_setting("opensubtitles", "api_key", ""))
        form.addRow("OpenSubtitles API Key:", self.opensubtitles_edit)
        if os.environ.get("OPENSUBTITLES_API_KEY"):
            form.addRow("", self._env_var_note("OPENSUBTITLES_API_KEY"))

        layout.addLayout(form)

        button_row = QHBoxLayout()
        button_row.addStretch()
        self.save_button = QPushButton("Save")
        self.save_button.clicked.connect(self._on_save)
        button_row.addWidget(self.save_button)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        button_row.addWidget(self.cancel_button)
        layout.addLayout(button_row)

    def _env_var_note(self, var_name: str) -> QLabel:
        note = QLabel(f"{var_name} environment variable is currently set and takes priority over this value.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #b08a2a;")
        return note

    def _on_save(self) -> None:
        # Saved as typed, even if blank -- an emptied field correctly
        # represents "no key configured here" to each client's own
        # get_api_key() (which treats an empty string the same as
        # absent), so this doubles as the way to clear a previously-
        # saved key.
        set_setting("tmdb", "api_key", self.tmdb_edit.text().strip())
        set_setting("tvdb", "api_key", self.tvdb_edit.text().strip())
        set_setting("opensubtitles", "api_key", self.opensubtitles_edit.text().strip())
        self.accept()
