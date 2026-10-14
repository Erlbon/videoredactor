"""
ToolSettingsDialog: lets the user point directly at ffmpeg/ffprobe/
mkvpropedit/mkvmerge executables when PATH auto-detection fails --
e.g. a portable/no-admin install where PATH can't be modified at all.
The rows live in ToolPathsWidget, which the Preferences dialog also uses
as its Tools / Paths page. (The transcode defaults that used to sit here are
on the Preferences > Transcode page now.)

Each executable row shows the currently configured override (blank =
auto-detect via PATH), a live availability indicator, a Browse button,
and a Clear button to remove the override and revert to PATH
auto-detection. The rows are redactor_common.gui.tool_path_row's.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QDialog, QHBoxLayout, QPushButton, QVBoxLayout
from redactor_common.gui.tool_path_row import ToolPathRow
from redactor_common.gui.tool_path_row import ToolPathsWidget as SharedToolPathsWidget

from core.external_tools import (
    get_tool_override, set_tool_override, is_executable_available,
    KNOWN_EXECUTABLES,
)

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


INTRO = (
    "If ffmpeg or MKVToolNix aren't on your system PATH, point "
    "directly at their executables here. Leave blank to "
    "auto-detect via PATH (the default)."
)


def _row(exe_name: str, display_name: str) -> ToolPathRow:
    """One redactor_common ToolPathRow, stored at once (commit="immediate") in settings.ini. The probe reads
    the stored override, which works because the row stores before it probes."""
    return ToolPathRow(
        f"{display_name}:", exe_name, get_tool_override(exe_name),
        probe=lambda _path: is_executable_available(exe_name),
        on_change=lambda path: set_tool_override(exe_name, path),
        status_width=60,
    )


class ToolPathsWidget(SharedToolPathsWidget):
    """The executable-location rows (ffmpeg, MKVToolNix) as a plain widget: redactor_common's
    ToolPathsWidget over this app's tool list.

    Changes are saved immediately per row (Browse/Clear/editing finished write
    straight to settings.ini) rather than batched behind an OK button, so it
    behaves the same inside ToolSettingsDialog and on the Preferences page.
    """

    def __init__(self, parent=None):
        super().__init__([_row(exe, name) for exe, name in EXECUTABLE_ROWS], INTRO, parent=parent)


class ToolSettingsDialog(QDialog):
    """Usage: dialog = ToolSettingsDialog(parent=self); dialog.exec()
    Tools > External Tools. Changes are saved immediately per row; Done just
    closes."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Locate External Tools")
        self.resize(550, 320)

        layout = QVBoxLayout(self)
        self.paths = ToolPathsWidget()
        layout.addWidget(self.paths)
        # Kept as attributes of the dialog for the callers/tests that read them.
        self.path_edits = self.paths.path_edits
        self.status_labels = self.paths.status_labels

        button_row = QHBoxLayout()
        button_row.addStretch()
        self.done_button = QPushButton("Done")
        self.done_button.clicked.connect(self.accept)
        button_row.addWidget(self.done_button)
        layout.addLayout(button_row)


