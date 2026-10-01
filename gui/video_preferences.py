"""
gui/video_preferences.py

Tools > Preferences (Ctrl+,): redactor_common's shared PreferencesDialog with
this app's settings.

  Filenames    shared section (ASCII-safe names, zero-pad + width, Auto-Number padding)
  Transcode    CRF, audio bitrate, threads
  Duplicates   Find Duplicates sensitivity
  Tools / Paths  ffmpeg / MKVToolNix locations, IMDb database and dataset files
               (a self-saving page, like the Locate External Tools dialog)

API keys stay in their own dialog (Tools > API Keys): they live in the OS
credential store, not in the ini, so they don't belong in a dialog that
reads and writes plain settings.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLineEdit, QMessageBox,
    QPushButton, QVBoxLayout, QWidget,
)
from redactor_common.gui.preferences_dialog import PreferencesDialog

from core import imdb_settings
from core.preferences_backend import KEY_AUDIO_BITRATE, bitrate_ok, build_sections, make_backend
from gui.tool_settings_dialog import ToolPathsWidget

# Display names of the dataset files, in the order imdb_settings.SOURCE_KEYS lists them.
_DATASET_LABELS = {
    "basics": "title.basics",
    "ratings": "title.ratings",
    "episodes": "title.episode",
    "akas": "title.akas",
}


class ImdbPathsGroup(QGroupBox):
    """The IMDb database file and the dataset files it is built from. Saved
    as soon as a field is edited or browsed, like the tool rows."""

    def __init__(self, parent=None):
        super().__init__("IMDb database and dataset files", parent)
        form = QFormLayout(self)
        self.path_edit = self._row(form, "Database:", imdb_settings.load_database(), self._save, "file")
        sources = imdb_settings.load_sources()
        self.source_edits = {
            kind: self._row(form, f"{label}:", sources.get(kind, ""), self._save, "file")
            for kind, label in _DATASET_LABELS.items()
        }

    def _row(self, form: QFormLayout, label: str, value: str, on_change, mode: str) -> QLineEdit:
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        edit = QLineEdit(value)
        edit.editingFinished.connect(on_change)
        row.addWidget(edit)
        browse = QPushButton("Browse...")
        browse.clicked.connect(lambda _checked=False, e=edit: self._browse(e))
        row.addWidget(browse)
        form.addRow(label, holder)
        return edit

    def _browse(self, edit: QLineEdit) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose file", edit.text().strip(), "All Files (*)")
        if path:
            edit.setText(path)
            self._save()

    def _save(self) -> None:
        imdb_settings.save_database(self.path_edit.text())
        imdb_settings.save_sources({kind: edit.text() for kind, edit in self.source_edits.items()})


def build_paths_page() -> QWidget:
    page = QWidget()
    layout = QVBoxLayout(page)
    page.tools = ToolPathsWidget()
    page.imdb = ImdbPathsGroup()
    layout.addWidget(page.tools)
    layout.addWidget(page.imdb)
    layout.addStretch(1)
    return page


class VideoPreferencesDialog(PreferencesDialog):
    """The shared dialog plus the one rule a PrefSpec cannot state: the audio
    bitrate must look like an ffmpeg bitrate, or nothing is written."""

    def __init__(self, parent=None):
        super().__init__(
            build_sections(), make_backend(), parent,
            extra_pages=[("Tools / Paths", build_paths_page)],
        )

    def bitrate_problem(self) -> str:
        """A plain-language problem with the typed bitrate, or "" when fine."""
        text = str(self._rows[KEY_AUDIO_BITRATE].get()).strip()
        if bitrate_ok(text):
            return ""
        return 'The audio bitrate must be a number with an optional k or m, for example "128k".'

    def _warn(self, message: str) -> None:
        QMessageBox.warning(self, "Preferences", message)

    def _bitrate_blocks(self) -> bool:
        problem = self.bitrate_problem()
        if problem:
            self._warn(problem)
        return bool(problem)

    def apply(self) -> None:
        if self._bitrate_blocks():
            return
        # Store the trimmed text the check accepted.
        self.set_value(KEY_AUDIO_BITRATE, str(self._rows[KEY_AUDIO_BITRATE].get()).strip())
        super().apply()

    def accept(self) -> None:
        if self._bitrate_blocks():
            return
        super().accept()
