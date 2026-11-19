"""
gui/convert_dialog.py

Media > Convert...: the ONE conversion entry (it replaces Remux to MP4, Convert to MP4 (H.264) and Convert to MP4/MKV
(Rules)). The dialog picks the mode (core/convert_modes.py), whether to dry run first, and whether the original stays
where it is; what happens to the original otherwise is the shared policy of Preferences > Conversion, shown here in one
line. The run itself is MainWindow._convert_paths.
"""

from __future__ import annotations

import os
from typing import Optional

from PyQt6.QtWidgets import (
    QButtonGroup, QCheckBox, QDialog, QDialogButtonBox, QLabel, QRadioButton, QVBoxLayout, QWidget,
)

from core import convert_modes as cm
from core import library_safety
from core.conversion_plan import RunOptions
from core.library_safety import SafetySettings
from redactor_common.gui.owned_dialog import OwnedDialog


def originals_sentence(safety: SafetySettings) -> str:
    """Where the original goes when it is not kept, in one line (the Preferences > Conversion policy)."""
    if safety.delete_after_conversion:
        return "The original is DELETED after a verified conversion (Preferences > Library safety)."
    if safety.originals == "recycle" and not safety.quarantine_root:
        return "The original goes to the Recycle Bin after a verified conversion."
    if safety.quarantine_root:
        return f"The original is moved to the quarantine folder {safety.quarantine_root} (with an undo log)."
    return "The original is moved to a dated 'originals' folder beside the file (with an undo log)."


class ConvertDialog(OwnedDialog):
    """After exec(): `mode`, `dry_run`, `keep` and `options` (lenient mode, music videos to 1080p) hold the choices."""

    def __init__(self, paths: list[str], parent: Optional[QWidget] = None, *, mode: str = cm.MODE_RULES,
                 safety: Optional[SafetySettings] = None):
        super().__init__(parent)
        self.setWindowTitle("Convert")
        self._safety = safety or library_safety.get_safety_settings()
        layout = QVBoxLayout(self)
        names = ", ".join(os.path.basename(p) for p in paths[:3]) + (f" and {len(paths) - 3} more" if len(paths) > 3 else "")
        layout.addWidget(QLabel(f"Convert {len(paths)} file(s): {names}"))

        self._buttons: dict[str, QRadioButton] = {}
        group = QButtonGroup(self)
        for key in cm.MODES:
            button = QRadioButton(cm.MODE_LABELS[key])
            button.setChecked(key == mode)
            button.toggled.connect(self._sync)
            group.addButton(button)
            layout.addWidget(button)
            self._buttons[key] = button
        self._help = QLabel()
        self._help.setWordWrap(True)
        self._help.setStyleSheet("color: gray;")
        layout.addWidget(self._help)

        # This run's choices (conversion_plan.RunOptions): both off every time the dialog opens.
        self._mv_1080 = QCheckBox("Scale music videos to 1080p HEVC")
        self._mv_1080.setToolTip("Every music video taller than 1080p is scaled to 1080p HEVC in this run, whatever its "
                                 "bit rate. Off: 4K is kept (re-encoded only above the Preferences > Conversion limit).")
        layout.addWidget(self._mv_1080)
        self._lenient = QCheckBox("Lenient mode for damaged files (a shortened result is kept beside the original)")
        self._lenient.setToolTip(
            "ffmpeg ignores decode errors and drops broken packets. A result of at least 60% of the original's "
            "length that passes every other check is kept as <name>.partial.mkv/.mp4 beside the original, which "
            "is never replaced by it. A full-length result replaces the original as usual.")
        layout.addWidget(self._lenient)

        self._keep = QCheckBox("Keep the original where it is (the new file gets its own name)")
        layout.addWidget(self._keep)
        self._fate = QLabel()
        self._fate.setWordWrap(True)
        layout.addWidget(self._fate)
        self._dry = QCheckBox("Dry run first: list what would happen and change nothing")
        self._dry.setChecked(self._safety.dry_run)
        layout.addWidget(self._dry)
        layout.addWidget(QLabel("Every result is verified before the original is touched. Preferences > Conversion sets "
                                "the defaults (what happens to originals, dry run, the rules)."))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._keep_touched = False
        self._keep.clicked.connect(self._on_keep_clicked)  # only a real click: a mode change does not count
        self._sync()

    def _on_keep_clicked(self, _checked: bool = False) -> None:
        self._keep_touched = True  # from now on the mode no longer decides the box
        self._update_fate()

    def _sync(self) -> None:
        mode = self.mode
        self._help.setText(cm.MODE_HELP[mode])
        self._mv_1080.setEnabled(mode == cm.MODE_RULES)  # the other modes do not use the bitrate rules
        if not self._keep_touched:
            # A lossy re-encode keeps the original unless told otherwise; the other modes follow the policy.
            self._keep.blockSignals(True)
            self._keep.setChecked(mode == cm.MODE_H264 or self._safety.keep_originals)
            self._keep.blockSignals(False)
        self._update_fate()

    def _update_fate(self) -> None:
        self._fate.setText("" if self._keep.isChecked() else originals_sentence(self._safety))

    @property
    def mode(self) -> str:
        return next((key for key, button in self._buttons.items() if button.isChecked()), cm.MODE_RULES)

    @property
    def dry_run(self) -> bool:
        return self._dry.isChecked()

    @property
    def keep(self) -> bool:
        return self._keep.isChecked()

    @property
    def options(self) -> RunOptions:
        return RunOptions(
            lenient=self._lenient.isChecked(),
            music_video_1080=self._mv_1080.isChecked() and self.mode == cm.MODE_RULES,
        )
