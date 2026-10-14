"""
gui/video_preferences.py

Tools > Preferences (Ctrl+,): redactor_common's shared PreferencesDialog with
this app's settings.

  Filenames    shared section (ASCII-safe names, zero-pad + width, Auto-Number padding)
  Transcode    CRF, audio bitrate, threads (Media > Convert to MP4 (H.264) only)
  Duplicates   Find Duplicates sensitivity
  Conversion   the conversion rules: bitrate/size limits, NVENC quality, disc images, subtitle languages
  Library safety quarantine folder, delete-after-conversion, recent-file guard,
               verification tolerance
  Tools / Paths  ffmpeg / MKVToolNix locations, IMDb database and dataset files
               (a self-saving page, like the Locate External Tools dialog)
  History      the opt-in switch for Tools > History (off by default) and Delete History

Conversion, Library safety and History are written on OK / Apply and dropped on Cancel,
like the shared pages (VideoPreferencesDialog.apply saves them); only Tools /
Paths still saves as you type.

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

from core import conversion_plan, history, imdb_settings, library_safety
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
    """Library safety: the quarantine folder, delete-after-conversion, the recent-file guard and the verification
    tolerance (core/library_safety.py). Saved by save(), which the dialog calls on OK / Apply; Cancel drops the
    edits."""

    def __init__(self, parent=None):
        super().__init__(parent)
        s = library_safety.get_safety_settings()
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Replaced files go to the Recycle Bin. If you set a quarantine folder they are moved there instead "
            "(mirroring their library path, in a dated subfolder), and each move is written to a TSV undo log. "
            "On a network share or removable drive the Recycle Bin is not available: without a quarantine folder "
            "the original is then kept beside the result as <name>.redact-orig.<ext>. "
            "These rules apply to the conversion rules (the Conversion page) and the Redact convert step."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        self.quarantine_edit = self._folder_row(form, "Quarantine folder (optional):", s.quarantine_root)
        self.undo_edit = self._folder_row(form, "Undo log folder:", s.undo_log_folder)
        # No dry-run option here: Redact's confirm and Media > Convert to MP4/MKV (Rules) ask it per run, so a
        # stale saved setting can never decide. The stored value is kept as it is (nothing reads it).
        self._stored_dry_run = s.dry_run
        self.delete_after = QCheckBox("Delete the original after a verified conversion (instead of quarantining it; can't be undone)")
        self.delete_after.setChecked(s.delete_after_conversion)
        form.addRow("", self.delete_after)
        self.recent = QSpinBox()
        self.recent.setRange(0, 1440)
        self.recent.setValue(s.recent_minutes)
        self.recent.setSuffix(" minutes")
        form.addRow("Leave files modified in the last:", self.recent)
        self.tol_seconds = QDoubleSpinBox()
        self.tol_seconds.setRange(0, 600)
        self.tol_seconds.setValue(s.duration_tolerance_seconds)
        self.tol_seconds.setSuffix(" s")
        self.tol_seconds.setToolTip("A converted file whose length differs from the original by more than this is "
                                    "rejected and the original kept.")
        form.addRow("Duration tolerance after a conversion:", self.tol_seconds)
        self.tol_percent = QDoubleSpinBox()
        self.tol_percent.setRange(0, 50)
        self.tol_percent.setValue(s.duration_tolerance_fraction * 100)
        self.tol_percent.setSuffix(" %")
        form.addRow("...or this share of the length (the larger counts):", self.tol_percent)
        layout.addLayout(form)
        layout.addStretch(1)

    def _folder_row(self, form: QFormLayout, label: str, value: str) -> QLineEdit:
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        edit = QLineEdit(value)
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

    def save(self) -> None:
        """Writes the page (the dialog's OK / Apply)."""
        library_safety.set_safety_settings(library_safety.SafetySettings(
            quarantine_root=self.quarantine_edit.text().strip(),
            dry_run=self._stored_dry_run,
            delete_after_conversion=self.delete_after.isChecked(),
            recent_minutes=self.recent.value(),
            duration_tolerance_seconds=self.tol_seconds.value(),
            duration_tolerance_fraction=self.tol_percent.value() / 100,
            undo_log_folder=self.undo_edit.text().strip(),
        ))


HISTORY_CHECKBOX = "Keep a history of tag changes (lets you undo a Save or Redact later)"


def history_help_text(path: str) -> str:
    return (
        "What is kept: the old and new text of every tag field a Save, Save All, Redact or the command line "
        "changes; never covers or other images. How much: the last 90 days, at most 50,000 changes. "
        f"Where: {path}. Tools > History shows it and can write old values back. You can switch it off again "
        "at any time; what was recorded stays until you delete it."
    )


class HistoryPage(QWidget):
    """Tools > History's recording switch (core/history.py, opt-in, off by default) and Delete history. The
    switch is written by save() on OK / Apply, like the Library safety page; Delete history acts at once, after
    a confirmation."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.enabled = QCheckBox(HISTORY_CHECKBOX)
        self.enabled.setChecked(history.history_enabled())
        layout.addWidget(self.enabled)
        self.help = QLabel(history_help_text(history.journal_path()))
        self.help.setWordWrap(True)
        self.help.setStyleSheet("color: gray;")
        layout.addWidget(self.help)
        row = QHBoxLayout()
        self.delete_button = QPushButton("Delete History...")
        self.delete_button.clicked.connect(self.delete_history)
        row.addWidget(self.delete_button)
        self.status = QLabel("")
        row.addWidget(self.status, 1)
        layout.addLayout(row)
        layout.addStretch(1)
        self._refresh_delete()

    def _refresh_delete(self) -> None:
        self.delete_button.setEnabled(history.journal_exists())

    def delete_history(self) -> bool:
        """Asks, then removes the journal file. Returns whether it was deleted."""
        if not history.journal_exists():
            self._refresh_delete()
            return False
        answer = QMessageBox.question(
            self, "Delete History",
            "Delete the whole history of tag changes? Old values recorded so far can no longer be restored.\n\n"
            f"{history.journal_path()}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False
        problem = history.delete_history()
        if problem:
            QMessageBox.warning(self, "Delete History", f"The history could not be deleted: {problem}")
            return False
        self.status.setText("History deleted.")
        self._refresh_delete()
        return True

    def save(self) -> None:
        """Writes the switch (the dialog's OK / Apply)."""
        if self.enabled.isChecked() != history.history_enabled():
            history.set_history_enabled(self.enabled.isChecked())


_DISC_LABELS = {
    conversion_plan.DISC_LEAVE: "Leave alone (never touched)",
    conversion_plan.DISC_LIST: "Leave alone, but list them for review",
    conversion_plan.DISC_REMUX_VOB: "Stream-copy a single .vob to MKV (ISO/BIN/IMG are only listed)",
}


MANY_SUBS_HELP = (
    "A file with this many subtitle tracks or more is converted to MKV, not MP4 (MP4 players handle many "
    "tracks badly). Default: 4."
)
DISC_HELP = (
    "Default: Leave alone. ISO, BIN, IMG and VOB disc images are then never converted and not listed; "
    "'list them' names each one in the report for a manual decision. Only the third choice converts anything "
    "(a single .vob, by stream copy to MKV)."
)


class ConversionPage(QWidget):
    """Conversion rules (core/conversion_plan.py): bitrate/size limits, the NVENC quality, disc images and the
    subtitle language whitelist. Saved by save() on OK / Apply, like the Library safety page."""

    def __init__(self, parent=None):
        super().__init__(parent)
        s = conversion_plan.get_conversion_settings()
        layout = QVBoxLayout(self)
        intro = QLabel(
            "The conversion rules, used by Media > Convert to MP4/MKV (Rules), File > Import and Convert, the Open "
            "Folder offer and the Redact step 'Convert to MP4/MKV and ingest subtitles'. Only MP4 and MKV are "
            "produced; sane codecs are copied, never re-encoded. A series episode is a file tagged TV, or an "
            "untagged file whose name reads as an episode (S01E02). The Transcode page is something else: the "
            "H.264 settings of Media > Convert to MP4 (H.264) only."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        self.movies = QCheckBox("Apply the bitrate rules to movies too (series episodes only by default)")
        self.movies.setChecked(s.apply_to_movies)
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
        form.addRow("hevc_nvenc quality (cq):", self.cq)
        self.ratio = self._dspin(form, "Keep the source unless the result is under:", s.output_max_ratio * 100, " % of it")
        self.many_subs = self._spin(form, "MKV when a file has at least:", 1, 99, s.many_subtitle_tracks, " subtitle tracks")
        self.many_subs_help = self._help(form, MANY_SUBS_HELP)
        self.disc = QComboBox()
        for key in conversion_plan.DISC_POLICIES:
            self.disc.addItem(_DISC_LABELS[key], key)
        self.disc.setCurrentIndex(max(0, self.disc.findData(s.disc_images)))
        form.addRow("Disc images (.vob .iso .bin .img):", self.disc)
        self.disc_help = self._help(form, DISC_HELP)
        self.languages = QLineEdit(s.subtitle_languages)
        self.languages.setPlaceholderText("all")
        self.languages.setToolTip('"all", or ISO codes separated by commas, for example: en, nl, de')
        form.addRow("Subtitle languages to ingest:", self.languages)
        layout.addLayout(form)
        layout.addStretch(1)

    @staticmethod
    def _help(form: QFormLayout, text: str) -> QLabel:
        """A one-line help text under the control above it."""
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet("color: gray;")
        form.addRow("", label)
        return label

    def _spin(self, form, label, low, high, value, suffix) -> QSpinBox:
        box = QSpinBox()
        box.setRange(low, high)
        box.setValue(value)
        box.setSuffix(suffix)
        form.addRow(label, box)
        return box

    def _dspin(self, form, label, value, suffix) -> QDoubleSpinBox:
        box = QDoubleSpinBox()
        box.setRange(0, 100000)
        box.setDecimals(1)
        box.setValue(value)
        box.setSuffix(suffix)
        form.addRow(label, box)
        return box

    def save(self) -> None:
        """Writes the page (the dialog's OK / Apply)."""
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
        # The shared dialog neither reads nor writes extra pages; these two are written by apply() below, so
        # Cancel drops their edits like on every shared page (they used to save on every click).
        deferred: list[QWidget] = []

        def deferred_page(factory):
            def build() -> QWidget:
                deferred.append(factory())
                return deferred[-1]
            return build

        super().__init__(
            build_sections(), make_backend(), parent,
            extra_pages=[
                ("Tools / Paths", build_paths_page),
                ("Conversion", deferred_page(ConversionPage)),
                ("Library safety", deferred_page(SafetyPage)),
                ("History", deferred_page(HistoryPage)),
            ],
        )
        self.conversion_page, self.safety_page, self.history_page = deferred

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
        self.conversion_page.save()
        self.safety_page.save()
        self.history_page.save()

    def accept(self) -> None:
        if self._bitrate_blocks():
            return
        super().accept()
