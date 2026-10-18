"""
gui/track_language_dialog.py

Metadata > Set Track Language...: one or more MP4 / MKV files, a language per track, applied IN PLACE (core/track_language.py:
no re-encoding, nothing but the language tags touched). With several files the rows are track POSITIONS ("Audio 1
(aac) in 12 files: currently und x 10, eng x 2"), one language choice per row. "Set all unlabelled audio tracks to ..."
is the one-click way; it changes only tracks that have no language yet. The apply runs per file under a cancellable
progress dialog; a failing file is listed with its reason and the others still run.
"""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)
from redactor_common.gui.background_call import call_in_background
from redactor_common.gui.progress import run_with_progress
from redactor_common.gui.sortable_table import enable_header_sort, natural_key
from redactor_common.gui.table_columns import make_columns_resizable

from core import track_language as tl
from core import track_language_codes as codes

TITLE = "Set Track Language"
KEEP = ""  # a row's combo data: leave the tracks alone


@dataclass
class _Row:
    kind: str
    position: int
    members: list = field(default_factory=list)  # [(path, TrackInfo)]
    combo: Optional[QComboBox] = None
    only_unlabelled: bool = False  # set by the one-click button: tracks that already have a language keep it


def _language_combo() -> QComboBox:
    combo = QComboBox()
    combo.addItem("(leave as it is)", KEEP)
    for code, label in codes.choices():
        combo.addItem(label, code)
    return combo


def current_summary(languages: list[str]) -> str:
    """'und' for one file; 'und x 10, eng x 2' for several."""
    counts = Counter(languages)
    if len(languages) == 1:
        return codes.label(languages[0])
    return ", ".join(f"{code} x {n}" for code, n in counts.most_common())


def row_label(row: _Row) -> str:
    codecs = Counter(track.codec for _, track in row.members)
    codec = "/".join(c for c, _ in codecs.most_common() if c) or "?"
    text = f"{row.kind.capitalize()} {row.position} ({codec})"
    if len(row.members) > 1:
        return f"{text} in {len(row.members)} files"
    title = row.members[0][1].title
    return f"{text}  \"{title}\"" if title else text


class TrackLanguageDialog(QDialog):
    """`entries` = [(path, [TrackInfo])] of the files to edit. After accept, `plan()` is what to apply."""

    play_requested = pyqtSignal(str, int)  # path, 0-based position among the file's AUDIO tracks

    def __init__(self, entries: list, parent=None, can_play: bool = False):
        super().__init__(parent)
        self.setWindowTitle(TITLE)
        # Window-modal (not application-modal): the player opened by "Play this track" is a sibling window and must
        # stay usable (A key, play/pause) while this dialog is open.
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.resize(760, 460)
        self.entries = entries
        self.rows: list[_Row] = []
        by_position: dict[tuple[str, int], _Row] = {}
        for path, tracks in entries:
            for track in tracks:
                row = by_position.get((track.kind, track.position))
                if row is None:
                    row = by_position[(track.kind, track.position)] = _Row(track.kind, track.position)
                row.members.append((path, track))
        order = {kind: n for n, kind in enumerate(tl.KINDS)}
        self.rows = sorted(by_position.values(), key=lambda r: (order[r.kind] if r.kind != "video" else 9, r.position))
        self.play_buttons: list[QPushButton] = []

        layout = QVBoxLayout(self)
        count = len(entries)
        names = entries[0][0] if count == 1 else ""
        layout.addWidget(QLabel(
            f"<b>{count} file(s) selected</b>" + (f": {os.path.basename(names)}" if names else "") + "<br>"
            "The language tag of each track is written inside the file, without re-encoding or rewriting it; "
            "nothing else is touched. Unsaved edits of the selected files are not saved and stay pending."))

        self.table = QTableWidget(len(self.rows), 4 if (can_play and count == 1) else 3)
        headers = ["Track", "Currently", "Set to"] + (["Listen"] if self.table.columnCount() == 4 else [])
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.verticalHeader().setVisible(False)
        self._fill_table()
        enable_header_sort(self.table, self._sort_rows)  # cell widgets: Qt cannot sort these natively
        make_columns_resizable(self.table, wide_column=0)
        layout.addWidget(self.table)

        one_click = QHBoxLayout()
        one_click.addWidget(QLabel("Set all unlabelled audio tracks to"))
        self.all_combo = QComboBox()
        for code, label in codes.choices():
            self.all_combo.addItem(label, code)
        self.all_combo.setCurrentIndex(0)
        one_click.addWidget(self.all_combo)
        self.all_button = QPushButton("Set")
        self.all_button.clicked.connect(self.set_all_unlabelled)
        one_click.addWidget(self.all_button)
        one_click.addStretch(1)
        layout.addLayout(one_click)
        self.subtitle_box = QCheckBox("Also set unlabelled subtitle tracks")
        layout.addWidget(self.subtitle_box)

        self.preview = QLabel("")
        self.preview.setWordWrap(True)
        layout.addWidget(self.preview)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.apply_button = buttons.addButton("Apply", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._update_preview()

    def _fill_table(self) -> None:
        """(Re)build the rows in self.rows order. A row's chosen language survives: the combos are made again, since
        refilling the table deletes the old cell widgets."""
        chosen = {id(r): (r.combo.currentData() if r.combo else None) for r in self.rows}
        self.table.setRowCount(0)
        self.table.setRowCount(len(self.rows))
        self.play_buttons.clear()
        for index, row in enumerate(self.rows):
            self.table.setItem(index, 0, QTableWidgetItem(row_label(row)))
            self.table.setItem(index, 1, QTableWidgetItem(current_summary([t.language for _, t in row.members])))
            row.combo = _language_combo()
            row.combo.activated.connect(lambda _i, r=row: self._chosen_by_hand(r))
            row.combo.currentIndexChanged.connect(self._update_preview)
            self.table.setCellWidget(index, 2, row.combo)
            if self.table.columnCount() == 4:
                if row.kind == "audio":
                    button = QPushButton("Play this track")
                    button.setToolTip("Open the player on this file with this audio track active, to hear what language it is")
                    button.clicked.connect(
                        lambda _checked=False, p=row.members[0][0], n=row.position - 1: self.play_requested.emit(p, n))
                    self.play_buttons.append(button)
                    self.table.setCellWidget(index, 3, button)
        for row in self.rows:
            if chosen.get(id(row)) is not None and row.combo is not None:
                row.combo.blockSignals(True)
                row.combo.setCurrentIndex(max(row.combo.findData(chosen[id(row)]), 0))
                row.combo.blockSignals(False)

    def _sort_rows(self, column: int, order: Qt.SortOrder) -> None:
        """Header click: sort the rows (the records) and rebuild the table; every row keeps its own combo choice."""
        def key(row: _Row):
            if column == 0:
                return natural_key(row_label(row))
            if column == 1:
                return natural_key(current_summary([t.language for _, t in row.members]))
            if column == 2:
                return natural_key(row.combo.currentText() if row.combo else "")
            return (row.kind, row.position)

        self.rows.sort(key=key, reverse=order == Qt.SortOrder.DescendingOrder)
        self._fill_table()
        make_columns_resizable(self.table, wide_column=0)
        self._update_preview()

    # --- the choices -----------------------------------------------------------------------------------------------

    def _chosen_by_hand(self, row: _Row) -> None:
        row.only_unlabelled = False  # a language picked on a row applies to every track of that row
        self._update_preview()

    def choose(self, kind: str, position: int, language: str) -> None:
        """Programmatic choice for a row (tests, and the one-click button)."""
        for row in self.rows:
            if (row.kind, row.position) == (kind, position):
                row.combo.setCurrentIndex(max(row.combo.findData(language), 0))
                row.only_unlabelled = False
        self._update_preview()

    def set_all_unlabelled(self) -> None:
        language = self.all_combo.currentData()
        kinds = ["audio"] + (["subtitle"] if self.subtitle_box.isChecked() else [])
        for row in self.rows:
            if row.kind in kinds and any(track.unlabelled for _, track in row.members):
                row.combo.setCurrentIndex(row.combo.findData(language))
                row.only_unlabelled = True
        self._update_preview()

    def plan(self) -> dict[str, dict[int, str]]:
        """{path: {stream index: language}} of what the choices would change (tracks that already have the language
        are left out)."""
        result: dict[str, dict[int, str]] = {}
        for row in self.rows:
            language = row.combo.currentData() if row.combo else KEEP
            if not language:
                continue
            for path, track in row.members:
                if track.language == language or (row.only_unlabelled and not track.unlabelled):
                    continue
                result.setdefault(path, {})[track.index] = language
        return result

    def _update_preview(self) -> None:
        plan = self.plan()
        tracks = sum(len(v) for v in plan.values())
        self.preview.setText(
            f"{tracks} track(s) in {len(plan)} file(s) will change; nothing else is touched." if tracks
            else "Nothing will change yet: pick a language for a track.")
        self.apply_button.setEnabled(bool(tracks))


# ---------------------------------------------------------------------------
# The flow (called by the main window and by Check Files)
# ---------------------------------------------------------------------------

def _history_note() -> str:
    from core import history  # noqa: PLC0415

    return "" if history.history_enabled() else (
        " Tools > History is off, so this change was not recorded for Restore Old Values; "
        "setting the old language again undoes it.")


def read_tracks(window, files) -> tuple[list, list[str]]:
    """[(path, [TrackInfo])] of the files that can be read (progress dialog), and the problems of the others."""
    entries, problems = [], []

    def step(vf, _index: int) -> None:
        path = str(vf.path)
        reason = tl.unsupported_reason(path)
        if reason:
            problems.append(f"{vf.path.name}: {reason}")
            return
        try:
            entries.append((path, call_in_background(tl.list_tracks, path)))
        except Exception as exc:  # noqa: BLE001 -- listed, and the others still read
            problems.append(f"{vf.path.name}: {exc}")

    run_with_progress(window, files, step, "Reading the tracks...", threshold=1, cancellable=False,
                      label_for=lambda vf: f"Reading {vf.path.name}")
    return entries, problems


def apply_plan(window, files, plan: dict[str, dict[int, str]]) -> tuple[dict[str, list], list[tuple[str, str]], bool]:
    """Writes `plan` file by file (cancellable between files; a failure does not stop the batch). Returns
    ({path: [TrackChange]} done, [(path, reason)] failed, cancelled)."""
    by_path = {str(vf.path): vf for vf in files}
    todo = [by_path[p] for p in plan if p in by_path]
    done: dict[str, list] = {}
    failed: list[tuple[str, str]] = []
    cancel = {"flag": False}

    def step(vf, _index: int) -> None:
        if cancel["flag"]:
            return
        path = str(vf.path)
        try:
            result = call_in_background(tl.set_track_languages, path, plan[path])
        except Exception as exc:  # noqa: BLE001 -- TrackLanguageError or anything else: listed with its reason
            failed.append((path, str(exc)))
            return
        done[path] = result.changes

    finished = run_with_progress(
        window, todo, step, "Setting track languages...", threshold=1, cancellable=True,
        label_for=lambda vf: f"Setting the language in {vf.path.name}", on_cancel=lambda: cancel.update(flag=True),
    )
    return done, failed, cancel["flag"] or not finished


def refresh_after_write(window, files, changed_paths: set[str]) -> None:
    """Rows, and the Check Files result of the files that had one (the cheap check re-run, so the note goes away)."""
    from gui.file_check_dialog import check_files  # noqa: PLC0415

    touched = [vf for vf in files if str(vf.path) in changed_paths]
    rechecks = [vf for vf in touched if vf.check is not None]
    if rechecks:
        check_files(window, rechecks, [])
    window._refresh_table_rows()
    if window._selected_video_files():
        window._on_selection_changed()


def audio_hints(tracks) -> dict[int, dict[str, str]]:
    """{0-based audio position: {language, title, codec}} for the player's selector, from our own ffprobe read (Qt often
    reports no language or title: it cannot map codes like 'nor' and MP4 keeps no track titles)."""
    audio = [t for t in tracks if t.kind == "audio"]
    return {n: {"language": t.language, "title": t.title, "codec": t.codec} for n, t in enumerate(audio)}


def open_player(window, path: str, audio_position: int, tracks) -> None:
    """'Play this track': the shared Player on `path` with that audio track (0-based among the audio tracks) active, to
    listen and decide its language. Read-only."""
    from redactor_common.gui.media_preview import show_media_preview  # noqa: PLC0415

    show_media_preview(window, path, video=True, autoplay=True, initial_audio_track=audio_position,
                       track_labels=audio_hints(tracks))


def close_players() -> None:
    """Closes the Player window(s), if any (this module's own listening windows)."""
    try:
        from redactor_common.gui import media_preview  # noqa: PLC0415

        for player in list(media_preview._open_windows):
            player.close()
    except Exception:  # noqa: BLE001 -- no player (or no QtMultimedia) is not a problem here
        pass


def run_set_track_language(window, files, error_details: Callable[[list[str]], str],
                           play: Optional[Callable[[str, int], None]] = None) -> None:
    """The whole Metadata > Set Track Language flow for `files` (VideoFile objects)."""
    from PyQt6.QtWidgets import QMessageBox  # noqa: PLC0415

    files = [vf for vf in files if not vf.load_error]
    if not files:
        window.status_bar.showMessage("Set Track Language: select at least one file that loaded first")
        return
    entries, problems = read_tracks(window, files)
    if problems:
        QMessageBox.warning(window, TITLE, error_details(problems))
    if not entries:
        return
    by_path = dict(entries)
    if play is None:
        def play(path: str, audio_position: int) -> None:
            open_player(window, path, audio_position, by_path[path])

    dialog = TrackLanguageDialog(entries, window, can_play=len(entries) == 1)
    dialog.play_requested.connect(play)
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    close_players()  # a player holds the file open: it must not be open while the file is written
    if not accepted:
        window.status_bar.showMessage("Set Track Language: cancelled, nothing changed")
        return
    plan = dialog.plan()
    if not plan:
        return
    done, failed, cancelled = apply_plan(window, files, plan)
    refresh_after_write(window, files, set(done))
    tracks = sum(len(c) for c in done.values())
    message = f"Set the language of {tracks} tracks in {len(done)} files"
    if failed:
        message += f", {len(failed)} failed"
    if cancelled:
        message += ", cancelled (the remaining files were not touched)"
    window.status_bar.showMessage(message)
    if failed:
        QMessageBox.warning(window, "Some files were not changed",
                            error_details([f"{Path(p).name}: {reason}" for p, reason in failed]) + _history_note())
    elif done:
        window.status_bar.showMessage(message + "." + _history_note())
