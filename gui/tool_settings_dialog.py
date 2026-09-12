"""
ToolSettingsDialog: lets the user point directly at ffmpeg/ffprobe/
mkvpropedit/mkvmerge executables when PATH auto-detection fails --
e.g. a portable/no-admin install where PATH can't be modified at all.
Also holds the persisted defaults for the "Convert Selected to MP4
(H.264)" transcode feature (core/transcode_settings.py) -- grouped here
rather than in a dialog of its own since both are "set once, rarely
revisited" ffmpeg-adjacent settings.

Each executable row shows the currently configured override (blank =
auto-detect via PATH), a live availability indicator, a Browse button,
and a Clear button to remove the override and revert to PATH
auto-detection.

NOTE: not runnable in this sandbox -- no PyQt6. Syntax-checked and
reviewed only.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit,
    QPushButton, QLabel, QFileDialog, QWidget, QSpinBox, QGroupBox,
)

from core.external_tools import (
    get_tool_override, set_tool_override, is_executable_available,
    KNOWN_EXECUTABLES,
)
from core.transcode_settings import get_transcode_settings, set_transcode_settings, TranscodeSettings

# Display order and labels for the four executables this app shells
# out to. Order matches how they're introduced elsewhere (ffmpeg tools
# first, then MKVToolNix) for consistency with the startup warning
# dialog and BUILD.md's own ordering.
EXECUTABLE_ROWS = [
    ("ffmpeg", "ffmpeg"),
    ("ffprobe", "ffprobe"),
    ("mkvpropedit", "mkvpropedit"),
    ("mkvmerge", "mkvmerge"),
    ("mkvextract", "mkvextract"),
]

# Sanity check at import time: if this list and external_tools.py's
# KNOWN_EXECUTABLES ever drift apart (a new exe added to one but not
# the other), fail loudly here rather than silently only offering a
# settings row for some of the actually-used executables.
assert {exe for exe, _ in EXECUTABLE_ROWS} == KNOWN_EXECUTABLES, (
    "EXECUTABLE_ROWS and core.external_tools.KNOWN_EXECUTABLES have "
    "drifted out of sync -- update both together."
)


class ToolSettingsDialog(QDialog):
    """Usage: dialog = ToolSettingsDialog(parent=self); dialog.exec()
    Changes are saved immediately per-row (Browse/Clear write straight
    to settings.ini) rather than batched behind an OK button -- matches
    this app's existing pattern of poster/subtitle sidecar saves being
    immediate, non-staged actions, and means there's no "did my browse
    action actually get saved" ambiguity if the dialog is closed via
    the window X button instead of a Done button.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Locate External Tools")
        self.resize(550, 420)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "If ffmpeg or MKVToolNix aren't on your system PATH, point "
            "directly at their executables here. Leave blank to "
            "auto-detect via PATH (the default)."
        ))

        self.path_edits: dict[str, QLineEdit] = {}
        self.status_labels: dict[str, QLabel] = {}

        form_container = QWidget()
        form_layout = QFormLayout(form_container)
        for exe_name, display_name in EXECUTABLE_ROWS:
            row_widget = QWidget()
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(0, 0, 0, 0)

            path_edit = QLineEdit(get_tool_override(exe_name))
            path_edit.setPlaceholderText("(auto-detect via PATH)")
            path_edit.editingFinished.connect(
                lambda exe=exe_name: self._on_path_edited(exe)
            )
            self.path_edits[exe_name] = path_edit
            row_layout.addWidget(path_edit)

            browse_button = QPushButton("Browse...")
            browse_button.clicked.connect(lambda checked, exe=exe_name: self._on_browse(exe))
            row_layout.addWidget(browse_button)

            clear_button = QPushButton("Clear")
            clear_button.clicked.connect(lambda checked, exe=exe_name: self._on_clear(exe))
            row_layout.addWidget(clear_button)

            status_label = QLabel()
            status_label.setFixedWidth(60)
            self.status_labels[exe_name] = status_label
            row_layout.addWidget(status_label)

            form_layout.addRow(f"{display_name}:", row_widget)
        layout.addWidget(form_container)

        transcode_group = QGroupBox("Convert to MP4 (H.264) Defaults")
        transcode_form = QFormLayout(transcode_group)

        current = get_transcode_settings()

        self.crf_spin = QSpinBox()
        self.crf_spin.setRange(0, 51)  # libx264's full valid CRF range
        self.crf_spin.setValue(current.crf)
        self.crf_spin.setToolTip(
            "Lower = higher quality, larger file. 18-28 is the usual "
            "sane range; libx264's own default is 23."
        )
        self.crf_spin.valueChanged.connect(self._on_transcode_setting_changed)
        transcode_form.addRow("Video quality (CRF):", self.crf_spin)

        self.audio_bitrate_edit = QLineEdit(current.audio_bitrate)
        self.audio_bitrate_edit.setPlaceholderText("128k")
        self.audio_bitrate_edit.setToolTip('ffmpeg -b:a value, e.g. "128k" or "192k".')
        self.audio_bitrate_edit.editingFinished.connect(self._on_transcode_setting_changed)
        transcode_form.addRow("Audio bitrate:", self.audio_bitrate_edit)

        self.threads_spin = QSpinBox()
        self.threads_spin.setRange(0, 128)
        self.threads_spin.setSpecialValueText("Auto (let ffmpeg decide)")
        self.threads_spin.setValue(current.threads)
        self.threads_spin.valueChanged.connect(self._on_transcode_setting_changed)
        transcode_form.addRow("Threads:", self.threads_spin)

        layout.addWidget(transcode_group)

        button_row = QHBoxLayout()
        button_row.addStretch()
        self.done_button = QPushButton("Done")
        self.done_button.clicked.connect(self.accept)
        button_row.addWidget(self.done_button)
        layout.addLayout(button_row)

        self._refresh_all_status()

    def _on_path_edited(self, exe_name: str) -> None:
        path = self.path_edits[exe_name].text().strip()
        set_tool_override(exe_name, path)
        self._refresh_status(exe_name)

    def _on_browse(self, exe_name: str) -> None:
        # Windows executables filter is harmless-but-inapplicable on
        # non-Windows dev/test runs -- QFileDialog just shows all files
        # there instead, no crash either way.
        path, _ = QFileDialog.getOpenFileName(
            self, f"Locate {exe_name}", "", "Executables (*.exe);;All Files (*)"
        )
        if not path:
            return  # user cancelled -- leave the existing override untouched
        self.path_edits[exe_name].setText(path)
        set_tool_override(exe_name, path)
        self._refresh_status(exe_name)

    def _on_clear(self, exe_name: str) -> None:
        self.path_edits[exe_name].setText("")
        set_tool_override(exe_name, "")
        self._refresh_status(exe_name)

    def _refresh_status(self, exe_name: str) -> None:
        available = is_executable_available(exe_name)
        label = self.status_labels[exe_name]
        label.setText("\u2713 found" if available else "\u2717 missing")
        label.setStyleSheet(
            "color: #2a8f2a;" if available else "color: #b02a2a;"
        )

    def _refresh_all_status(self) -> None:
        for exe_name, _ in EXECUTABLE_ROWS:
            self._refresh_status(exe_name)

    def _on_transcode_setting_changed(self, *_args) -> None:
        """Saved immediately per-field, same as the executable overrides
        above -- no separate Done-triggered save step. Audio bitrate is
        stored as-typed with no validation (an empty/garbage value falls
        back to the module default the next time it's read, rather than
        being rejected here)."""
        set_transcode_settings(TranscodeSettings(
            crf=self.crf_spin.value(),
            audio_bitrate=self.audio_bitrate_edit.text().strip(),
            threads=self.threads_spin.value(),
        ))
