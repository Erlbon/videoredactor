"""
gui/duplicates_dialog.py

Media > Find Duplicates... (core/video_duplicates.py): hashes one
frame of every loaded file that has a same-length neighbour (on a worker
thread per file, under a cancellable progress dialog, so the window keeps
painting), then lists the groups of probable duplicates for review.

Review only: nothing is changed unless the user picks an action --
Reveal in folder, Open in the default app, Select these in the main list,
or (behind an explicit confirm) Move selected to the Recycle Bin. Nothing
is cached on disk; the hashes live only for this run.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)
from redactor_common.core.os_utils import open_with_default_app, reveal_in_file_manager
from redactor_common.core.trash import TrashError, move_to_trash
from redactor_common.gui.background_call import call_in_background
from redactor_common.gui.progress import run_with_progress

from core.config import get_setting, set_setting
from core.format_helpers import format_duration, format_file_size
from core.video_duplicates import (
    HAMMING_THRESHOLD,
    Candidate,
    candidates_needing_hash,
    frame_hash,
    group_duplicates,
)
from core.video_file import VideoFile

TITLE = "Find Duplicates"
FILE_ROLE = Qt.ItemDataRole.UserRole
COLUMNS = ["File", "Folder", "Duration", "Resolution", "Size", "Video codec"]


class DuplicatesDialog(QDialog):
    """The groups of probable duplicates. After exec(): `to_select` holds
    the files for "Select these in the list" (dialog accepted) and
    `trashed` the files already moved to the Recycle Bin."""

    def __init__(self, groups: list[list[VideoFile]], parent=None):
        super().__init__(parent)
        self.setWindowTitle(TITLE)
        self.resize(1000, 480)
        self.to_select: list[VideoFile] = []
        self.trashed: list[VideoFile] = []

        layout = QVBoxLayout(self)
        summary = QLabel(
            f"{len(groups)} group(s) of probable duplicates. Files in a group have nearly the same "
            "length and a matching picture; <b>you decide which to keep</b> -- nothing is changed "
            "until you choose an action below. Select the files you want to act on."
        )
        summary.setWordWrap(True)
        layout.addWidget(summary)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(len(COLUMNS))
        self.tree.setHeaderLabels(COLUMNS)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setRootIsDecorated(True)
        header = self.tree.header()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False)
        for col, width in enumerate((260, 300, 80, 90, 80, 90)):
            self.tree.setColumnWidth(col, width)
        for number, group in enumerate(groups, 1):
            parent_item = QTreeWidgetItem([f"Group {number} ({len(group)} files)"])
            self.tree.addTopLevelItem(parent_item)
            for vf in group:
                meta = vf.metadata
                row = QTreeWidgetItem([
                    vf.path.name, str(vf.path.parent), format_duration(meta.duration_seconds),
                    meta.resolution, format_file_size(vf.size_bytes), meta.video_codec,
                ])
                row.setData(0, FILE_ROLE, vf)
                row.setToolTip(0, str(vf.path))
                parent_item.addChild(row)
            parent_item.setExpanded(True)
        layout.addWidget(self.tree)

        buttons = QDialogButtonBox()
        self.reveal_button = buttons.addButton("Reveal in Folder", QDialogButtonBox.ButtonRole.ActionRole)
        self.open_button = buttons.addButton("Open", QDialogButtonBox.ButtonRole.ActionRole)
        self.select_button = buttons.addButton("Select These in the List", QDialogButtonBox.ButtonRole.AcceptRole)
        self.trash_button = buttons.addButton("Move Selected to Recycle Bin...", QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(QDialogButtonBox.StandardButton.Close)
        self.reveal_button.clicked.connect(self._reveal)
        self.open_button.clicked.connect(self._open)
        self.select_button.clicked.connect(self._select_in_list)
        self.trash_button.clicked.connect(self._trash)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_files(self) -> list[VideoFile]:
        files = []
        for item in self.tree.selectedItems():
            vf = item.data(0, FILE_ROLE)
            if vf is not None:
                files.append(vf)
        return files

    def _reveal(self) -> None:
        for vf in self.selected_files():
            reveal_in_file_manager(str(vf.path))

    def _open(self) -> None:
        for vf in self.selected_files():
            open_with_default_app(str(vf.path))

    def _select_in_list(self) -> None:
        self.to_select = self.selected_files()
        if self.to_select:
            self.accept()

    def _trash(self) -> None:
        files = self.selected_files()
        if not files:
            return
        names = "\n".join(vf.path.name for vf in files[:10]) + ("\n..." if len(files) > 10 else "")
        answer = QMessageBox.question(
            self, TITLE,
            f"Move {len(files)} file(s) to the Recycle Bin?\n\n{names}\n\nThey can be restored from the Recycle Bin.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        failures = []
        for vf in files:
            try:
                move_to_trash(str(vf.path))
            except (TrashError, OSError) as exc:
                failures.append(f"{vf.path.name}: {exc}")
                continue
            self.trashed.append(vf)
            self._remove_row(vf)
        if failures:
            QMessageBox.warning(self, TITLE, "Couldn't move to the Recycle Bin:\n\n" + "\n".join(failures))

    def _remove_row(self, vf: VideoFile) -> None:
        for g in range(self.tree.topLevelItemCount() - 1, -1, -1):
            group = self.tree.topLevelItem(g)
            for c in range(group.childCount() - 1, -1, -1):
                if group.child(c).data(0, FILE_ROLE) is vf:
                    group.takeChild(c)
            if group.childCount() < 2:  # a lone file is no longer a duplicate
                self.tree.takeTopLevelItem(g)


def find_duplicates_flow(window, files: list[VideoFile]) -> None:
    """The whole Media > Find Duplicates... flow. `window` is the main
    window (status bar, file list, row refresh, selection)."""
    threshold, ok = QInputDialog.getInt(
        window, TITLE,
        "Two videos match when their picture hashes differ in at most this many of 64 bits\n"
        "(lower = stricter; 6 is a good start):",
        int(get_setting("duplicates", "threshold", str(HAMMING_THRESHOLD)) or HAMMING_THRESHOLD), 0, 32,
    )
    if not ok:
        return
    set_setting("duplicates", "threshold", str(threshold))

    candidates = [Candidate(vf, vf.metadata.duration_seconds) for vf in files if not vf.load_error]
    needed = candidates_needing_hash(candidates)
    if not needed:
        QMessageBox.information(window, TITLE, "No two loaded files have the same length, so there are no duplicates to find.")
        return

    unreadable: list[str] = []

    def step(candidate: Candidate, _index: int) -> None:
        vf: VideoFile = candidate.key  # type: ignore[assignment]
        try:
            candidate.hash = call_in_background(frame_hash, str(vf.path), candidate.duration)
        except Exception:  # an unreadable file just drops out of the comparison
            candidate.hash = None
        if candidate.hash is None:
            unreadable.append(vf.path.name)

    finished = run_with_progress(
        window, needed, step, "Comparing videos...", threshold=1, cancellable=True,
        label_for=lambda c: f"Reading a frame of {c.key.path.name}",
    )
    if not finished:
        window.status_bar.showMessage("Find Duplicates cancelled")
        return

    groups = [[c.key for c in group] for group in group_duplicates(candidates, threshold)]
    note = f" ({len(unreadable)} file(s) couldn't be read)" if unreadable else ""
    if not groups:
        window.status_bar.showMessage(f"No duplicates found among {len(needed)} file(s) of matching length{note}")
        QMessageBox.information(window, TITLE, f"No duplicates found.{note}")
        return
    window.status_bar.showMessage(f"Found {len(groups)} group(s) of probable duplicates{note}")
    dialog = DuplicatesDialog(groups, window)
    result = dialog.exec()
    if dialog.trashed:
        gone = {id(vf) for vf in dialog.trashed}
        window.video_files = [vf for vf in window.video_files if id(vf) not in gone]
        window._refresh_table_rows()
        window.status_bar.showMessage(f"Moved {len(dialog.trashed)} file(s) to the Recycle Bin")
    if result == QDialog.DialogCode.Accepted and dialog.to_select:
        window._reselect_files([id(vf) for vf in dialog.to_select if id(vf) not in {id(t) for t in dialog.trashed}])
