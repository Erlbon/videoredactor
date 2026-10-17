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
    QDialog, QVBoxLayout, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QWidget,
)

from core import api_keys, scene_lookup
from core.config import remove_setting
from redactor_common.gui.progress import run_busy
from redactor_common.gui.secret_field import SecretField
from redactor_common.gui.secret_prompts import save_with_fallback_prompt


# The keys with a "Check the key" button (one cheap authenticated request each).
CHECKABLE = ("theporndb", "stashdb")


class ApiKeysDialog(QDialog):
    """Usage: dialog = ApiKeysDialog(parent=self); dialog.exec() --
    saves directly on click (Save button), no further caller action
    needed; nothing needs to refresh elsewhere in response, since each
    key is only read at the moment an import is actually attempted.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("API Keys")
        self.resize(560, 420)

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Enter your own API keys for the lookup services: TMDB, TheTVDB, OpenSubtitles, and (optionally) "
            "ThePornDB and StashDB. They are free to obtain from their websites. "
            "Keys are kept in your system's credential store, not in a settings file, and are never exported "
            "with File > Export Settings."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.fields: dict[str, SecretField] = {}
        self.check_buttons: dict[str, QPushButton] = {}
        for key_id, (name, env_var, _, label) in api_keys.KEYS.items():
            field = SecretField(api_keys.APP, name, env_var=env_var)
            self.fields[key_id] = field
            if key_id in CHECKABLE:
                row = QWidget()
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(0, 0, 0, 0)
                row_layout.addWidget(field, 1)
                button = QPushButton("Check the key")
                button.setToolTip("Makes one small request to the service with the key typed here (or the saved one) "
                                  "and says whether it was accepted.")
                button.clicked.connect(lambda _checked=False, k=key_id: self._on_check(k))
                row_layout.addWidget(button)
                self.check_buttons[key_id] = button
                form.addRow(self._row_label(label), row)
            else:
                form.addRow(self._row_label(label), field)
            if key_id == "stashdb":
                self.stash_endpoint = QLineEdit(scene_lookup.stash_endpoint())
                self.stash_endpoint.setToolTip("The stash-box server the StashDB key belongs to. The default is "
                                               + scene_lookup.DEFAULT_STASH_ENDPOINT)
                form.addRow(self._row_label("StashDB address"), self.stash_endpoint)
        layout.addLayout(form)
        self.scene_note = QLabel(
            "ThePornDB and StashDB: only a file's hash (OSHASH) and/or the search text you see in the lookup dialog are "
            "sent, with your key to that service. Nothing else leaves this computer, and no image is downloaded unless "
            "you apply a result and tick Save the poster in the file."
        )
        self.scene_note.setWordWrap(True)
        self.scene_note.setStyleSheet("color: gray;")
        layout.addWidget(self.scene_note)

        button_row = QHBoxLayout()
        button_row.addStretch()
        self.save_button = QPushButton("Save")
        self.save_button.clicked.connect(self._on_save)
        button_row.addWidget(self.save_button)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        button_row.addWidget(self.cancel_button)
        layout.addLayout(button_row)

    @staticmethod
    def _row_label(text: str) -> QLabel:
        """A wrapping label of fixed width: a long one ("LibreTranslate API Key (optional, ...)") must not
        stretch the label column and push the key fields off the dialog."""
        label = QLabel(f"{text}:")
        label.setWordWrap(True)
        label.setFixedWidth(170)
        return label

    def _typed_key(self, key_id: str) -> str:
        return self.fields[key_id].new_value()

    def _on_check(self, key_id: str) -> None:
        """"Check the key": one cheap authenticated request on a worker thread (Cancel works), the exact reason
        on failure. Uses the key typed in the field, else the saved one; nothing is saved by this."""
        from core import stashdb_client, theporndb_client
        from core.scene_lookup import SceneLookupError

        typed = self._typed_key(key_id) or None
        if key_id == "stashdb":
            try:
                endpoint = scene_lookup.normalize_endpoint(self.stash_endpoint.text())
            except ValueError as exc:
                QMessageBox.warning(self, "StashDB", str(exc))
                return
            service, call = "StashDB", lambda: stashdb_client.check_key(typed, endpoint)
        else:
            service, call = "ThePornDB", lambda: theporndb_client.check_key(typed)
        try:
            message = run_busy(self, f"Checking the {service} key...", call, cancellable=True, on_cancel=None)
        except SceneLookupError as exc:
            QMessageBox.warning(self, f"{service} key", str(exc))
            return
        if message is not None:
            QMessageBox.information(self, f"{service} key", message)

    def _save(self, allow_unencrypted_fallback) -> None:
        for key_id, field in self.fields.items():
            if field.apply(allow_unencrypted_fallback):
                # A not-yet-migrated plaintext copy would otherwise come back
                # after a removal, or go stale after a replacement.
                remove_setting(api_keys.KEYS[key_id][2], "api_key")

    def _on_save(self) -> None:
        if hasattr(self, "stash_endpoint"):
            try:
                scene_lookup.normalize_endpoint(self.stash_endpoint.text())
            except ValueError as exc:
                QMessageBox.warning(self, "StashDB address", str(exc))
                return
        remember = api_keys.apply_fallback_preference()
        # Declined or failed: stay open with what was typed; nothing is lost.
        if save_with_fallback_prompt(self, self._save, remember, noun="API keys"):
            if hasattr(self, "stash_endpoint"):
                scene_lookup.set_stash_endpoint(self.stash_endpoint.text())
            self.accept()
