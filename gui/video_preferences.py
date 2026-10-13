"""
gui/video_preferences.py

Tools > Preferences (Ctrl+,): redactor_common's shared PreferencesDialog with
this app's settings.

  Filenames    shared section (ASCII-safe names, zero-pad + width, Auto-Number padding)
  Transcode    CRF, audio bitrate, threads
  Duplicates   Find Duplicates sensitivity
  Conversion   bitrate/size limits, NVENC quality, disc images, subtitle languages
  Library safety quarantine folder, dry-run default, recent-file guard, verification tolerance
  Tools / Paths  ffmpeg / MKVToolNix locations, IMDb database and dataset files
               (a self-saving page, like the Locate External Tools dialog)

API keys stay in their own dialog (Tools > API Keys): they live in the OS
credential store, not in the ini, so they don't belong in a dialog that
reads and writes plain settings.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)
from redactor_common.gui.preferences_dialog import PreferencesDialog

from core import conversion_plan, imdb_settings, library_safety
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


class SafetyPage(QWidget):
    """Library safety: the quarantine folder, dry-run default, the recent-file guard and the verification
    tolerance (core/library_safety.py). Self-saving like the Tools / Paths page."""

    def __init__(self, parent=None):
        super().__init__(parent)
        s = library_safety.get_safety_settings()
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Replaced files go to the Recycle Bin. If you set a quarantine folder they are moved there instead "
            "(mirroring their library path, in a dated subfolder), and each move is written to a TSV undo log."
        ))
        form = QFormLayout()
        self.quarantine_edit = self._folder_row(form, "Quarantine folder (optional):", s.quarantine_root)
        self.undo_edit = self._folder_row(form, "Undo log folder:", s.undo_log_folder)
        self.dry_run = QCheckBox("Dry run first: list what would change and touch nothing")
        self.dry_run.setChecked(s.dry_run)
        self.dry_run.toggled.connect(self._save)
        form.addRow("", self.dry_run)
        self.delete_after = QCheckBox("Delete the original after a verified conversion (instead of quarantining it; can't be undone)")
        self.delete_after.setChecked(s.delete_after_conversion)
        self.delete_after.toggled.connect(self._save)
        form.addRow("", self.delete_after)
        self.recent = QSpinBox()
        self.recent.setRange(0, 1440)
        self.recent.setValue(s.recent_minutes)
        self.recent.setSuffix(" minutes")
        self.recent.valueChanged.connect(self._save)
        form.addRow("Leave files modified in the last:", self.recent)
        self.tol_seconds = QDoubleSpinBox()
        self.tol_seconds.setRange(0, 600)
        self.tol_seconds.setValue(s.duration_tolerance_seconds)
        self.tol_seconds.setSuffix(" s")
        self.tol_seconds.valueChanged.connect(self._save)
        form.addRow("Duration tolerance after a conversion:", self.tol_seconds)
        self.tol_percent = QDoubleSpinBox()
        self.tol_percent.setRange(0, 50)
        self.tol_percent.setValue(s.duration_tolerance_fraction * 100)
        self.tol_percent.setSuffix(" %")
        self.tol_percent.valueChanged.connect(self._save)
        form.addRow("...or this share of the length (the larger counts):", self.tol_percent)
        layout.addLayout(form)
        layout.addStretch(1)

    def _folder_row(self, form: QFormLayout, label: str, value: str) -> QLineEdit:
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        edit = QLineEdit(value)
        edit.editingFinished.connect(self._save)
        row.addWidget(edit)
        browse = QPushButton("Browse...")
        browse.clicked.connect(lambda _checked=False, e=edit: self._browse(e))
        row.addWidget(browse)
        form.addRow(label, holder)
        return edit

    def _browse(self, edit: QLineEdit) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose folder", edit.text().strip())
        if path:
            edit.setText(path)
            self._save()

    def _save(self, *_args) -> None:
        library_safety.set_safety_settings(library_safety.SafetySettings(
            quarantine_root=self.quarantine_edit.text().strip(),
            dry_run=self.dry_run.isChecked(),
            delete_after_conversion=self.delete_after.isChecked(),
            recent_minutes=self.recent.value(),
            duration_tolerance_seconds=self.tol_seconds.value(),
            duration_tolerance_fraction=self.tol_percent.value() / 100,
            undo_log_folder=self.undo_edit.text().strip(),
        ))


_DISC_LABELS = {
    conversion_plan.DISC_LEAVE: "Leave alone (never touched)",
    conversion_plan.DISC_LIST: "Leave alone, but list them for review",
    conversion_plan.DISC_REMUX_VOB: "Stream-copy a single .vob to MKV (ISO/BIN/IMG are only listed)",
}


class ConversionPage(QWidget):
    """Conversion rules (core/conversion_plan.py): bitrate/size limits, the NVENC quality, disc images and the
    subtitle language whitelist. Self-saving like the Library safety page."""

    def __init__(self, parent=None):
        super().__init__(parent)
        s = conversion_plan.get_conversion_settings()
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Used by the Redact step 'Convert to MP4/MKV and ingest subtitles'. Only MP4 and MKV are produced; sane "
            "codecs are copied, never re-encoded."
        ))
        form = QFormLayout()
        self.movies = QCheckBox("Apply the bitrate rules to movies too (series episodes only by default)")
        self.movies.setChecked(s.apply_to_movies)
        self.movies.toggled.connect(self._save)
        form.addRow("", self.movies)
        self.min_size = self._spin(form, "Ignore files under:", 0, 100000, s.min_size_mb, " MB")
        self.scale_height = self._spin(form, "Scale taller video down to:", 360, 2160, s.scale_height, " p")
        self.scale_gb = self._dspin(form, "...when larger than:", s.scale_size_trigger_gb, " GB")
        self.scale_mbps = self._dspin(form, "...or video bitrate above:", s.scale_video_mbps, " Mb/s")
        self.reencode_mbps = self._dspin(form, "Re-encode (no scaling) at 720p or less above:", s.reencode_video_mbps, " Mb/s")
        self.reencode_small = self._dspin(form, "...and below 650p above:", s.reencode_video_mbps_small, " Mb/s")
        self.cq = QComboBox()
        for value, label in ((30, "30 (about 1.5 Mb/s)"), (27, "27 (about 2.3 Mb/s)")):
            self.cq.addItem(label, value)
        self.cq.setCurrentIndex(max(0, self.cq.findData(s.nvenc_cq)))
        self.cq.currentIndexChanged.connect(self._save)
        form.addRow("hevc_nvenc quality (cq):", self.cq)
        self.ratio = self._dspin(form, "Keep the source unless the result is under:", s.output_max_ratio * 100, " % of it")
        self.many_subs = self._spin(form, "MKV when a file has at least:", 1, 99, s.many_subtitle_tracks, " subtitle tracks")
        self.disc = QComboBox()
        for key in conversion_plan.DISC_POLICIES:
            self.disc.addItem(_DISC_LABELS[key], key)
        self.disc.setCurrentIndex(max(0, self.disc.findData(s.disc_images)))
        self.disc.currentIndexChanged.connect(self._save)
        form.addRow("Disc images (.vob .iso .bin .img):", self.disc)
        self.languages = QLineEdit(s.subtitle_languages)
        self.languages.setPlaceholderText("all")
        self.languages.setToolTip('"all", or ISO codes separated by commas, for example: en, nl, de')
        self.languages.editingFinished.connect(self._save)
        form.addRow("Subtitle languages to ingest:", self.languages)
        layout.addLayout(form)
        layout.addStretch(1)

    def _spin(self, form, label, low, high, value, suffix) -> QSpinBox:
        box = QSpinBox()
        box.setRange(low, high)
        box.setValue(value)
        box.setSuffix(suffix)
        box.valueChanged.connect(self._save)
        form.addRow(label, box)
        return box

    def _dspin(self, form, label, value, suffix) -> QDoubleSpinBox:
        box = QDoubleSpinBox()
        box.setRange(0, 100000)
        box.setDecimals(1)
        box.setValue(value)
        box.setSuffix(suffix)
        box.valueChanged.connect(self._save)
        form.addRow(label, box)
        return box

    def _save(self, *_args) -> None:
        current = conversion_plan.get_conversion_settings()
        current.apply_to_movies = self.movies.isChecked()
        current.min_size_mb = self.min_size.value()
        current.scale_height = self.scale_height.value()
        current.scale_size_trigger_gb = self.scale_gb.value()
        current.scale_video_mbps = self.scale_mbps.value()
        current.reencode_video_mbps = self.reencode_mbps.value()
        current.reencode_video_mbps_small = self.reencode_small.value()
        current.nvenc_cq = int(self.cq.currentData())
        current.output_max_ratio = self.ratio.value() / 100
        current.many_subtitle_tracks = self.many_subs.value()
        current.disc_images = self.disc.currentData()
        current.subtitle_languages = self.languages.text().strip() or "all"
        conversion_plan.set_conversion_settings(current)


class VideoPreferencesDialog(PreferencesDialog):
    """The shared dialog plus the one rule a PrefSpec cannot state: the audio
    bitrate must look like an ffmpeg bitrate, or nothing is written."""

    def __init__(self, parent=None):
        super().__init__(
            build_sections(), make_backend(), parent,
            extra_pages=[("Tools / Paths", build_paths_page), ("Conversion", ConversionPage), ("Library safety", SafetyPage)],
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
