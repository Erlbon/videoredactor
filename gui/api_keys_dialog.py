"""
ApiKeysDialog: "API Keys..." -- lets the user enter/edit the TMDB,
TheTVDB, and OpenSubtitles API keys directly in the app, rather than
needing to set environment variables.

The keys live in the OS credential store (redactor_common's secret
store, see core/api_keys.py), never in settings.ini. Each row is a
redactor_common SecretField: a masked edit that never shows the stored
value (empty = keep it), a Remove button, and a label naming where the
key comes from -- including "taken from an environment variable", which
takes priority over anything entered here.

With no usable credential store (no `keyring`, headless Linux) saving
raises SecretStoreUnavailable; the user is asked whether to use an
UNENCRYPTED file instead, and only a yes is remembered.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QHBoxLayout, QLabel, QMessageBox, QPushButton,
)

from core import api_keys
from core.config import remove_setting
from redactor_common.core import secret_store
from redactor_common.gui.secret_field import SecretField


class ApiKeysDialog(QDialog):
    """Usage: dialog = ApiKeysDialog(parent=self); dialog.exec() --
    saves directly on click (Save button), no further caller action
    needed; nothing needs to refresh elsewhere in response, since each
    key is only read at the moment an import is actually attempted.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("API Keys")
        self.resize(480, 320)

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Enter your own API keys for TMDB, TheTVDB, and OpenSubtitles "
            "import. All three are free to obtain from their respective websites. "
            "Keys are kept in your system's credential store, not in a settings file."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        self.fields: dict[str, SecretField] = {}
        for key_id, (name, env_var, _, label) in api_keys.KEYS.items():
            field = SecretField(api_keys.APP, name, env_var=env_var)
            self.fields[key_id] = field
            form.addRow(f"{label}:", field)
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

    def _ask_unencrypted_fallback(self) -> bool:
        answer = QMessageBox.question(
            self, "No secure storage available",
            "This computer has no system credential store the app can use to keep "
            "your API keys safely (Windows Credential Manager, macOS Keychain, or "
            "the Linux Secret Service).\n\n"
            "The keys can instead be saved in a plain, UNENCRYPTED file in your "
            "user profile folder. Anyone who can read your files could read them.\n\n"
            "Save the keys in an unencrypted file?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _on_save(self) -> None:
        for key_id, field in self.fields.items():
            try:
                changed = field.apply()
            except secret_store.SecretStoreUnavailable:
                if not self._ask_unencrypted_fallback():
                    # Stay open with what was typed; nothing is lost.
                    return
                api_keys.remember_unencrypted_fallback(True)
                changed = field.apply()
            if changed:
                # A not-yet-migrated plaintext copy would otherwise come back
                # after a removal, or go stale after a replacement.
                remove_setting(api_keys.KEYS[key_id][2], "api_key")
        self.accept()
