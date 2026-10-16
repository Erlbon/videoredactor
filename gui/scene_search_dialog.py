"""
SceneSearchDialog: candidate picker for the ThePornDB and StashDB lookups (Metadata > Look Up), in the style of the
TMDB picker: always shown, nothing is applied from here. The caller gets `selected_result` (and `save_cover`) and runs
the per-field overwrite review (redactor_common's resolve_overwrite_conflicts) before anything is written.

On open it asks the service for the selected file by its hash (the OSHASH, plus the MD5 only when Preferences allows
it): a "file match" row is highlighted, a title result is flagged "match by title: check it". With no file match the
search text, prefilled from the file name and editable, is searched automatically; after that only on Search. Every
request runs on a worker thread under the progress dialog, with Cancel.

With no key set the dialog says so, with where to get one and a button to Tools > API Keys.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QBrush, QColor, QFont
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QTextEdit, QVBoxLayout,
)
from redactor_common.gui.table_columns import make_columns_resizable

from core import api_keys, stashdb_client, theporndb_client
from core.scene_lookup import (
    KEY_HELP, TITLE_WARNING, FileHashes, SceneLookupError, SceneResult, file_hashes, md5_enabled,
    search_text_for,
)
from core.video_file import VideoFile
from gui.lookup import CANCELLED, run_cancellable_lookup

HEADERS = ["Match", "Title", "Studio", "Date", "Performers"]
COL_MATCH, COL_TITLE, COL_STUDIO, COL_DATE, COL_PERFORMERS = range(5)
FILE_MATCH_TINT = QColor(46, 160, 67, 70)  # translucent: readable on a light or a dark theme
FILE_MATCH_LABEL = "File match"
PRIVACY_NOTE = (
    "Only the file's hash and/or the search text below are sent to {name}, with your key. Nothing else leaves this "
    "computer, and nothing is downloaded until you apply a result."
)


@dataclass
class SceneSource:
    """One service, as the dialog uses it. The callables are looked up at call time so tests (and the clients'
    injectable fetch) see the live functions."""

    key_id: str
    name: str
    search_hash: Callable[[FileHashes], list]
    search_title: Callable[..., list]               # (text, year, kind) -> [SceneResult]
    download_cover: Callable[[str], bytes]
    errors: tuple = (SceneLookupError,)
    kinds: tuple = ()                               # (("scene", "Scene"), ...) -> a Type chooser
    uses_md5: bool = False                          # the service can match an MD5 too

    def has_key(self) -> bool:
        return bool(api_keys.get_key(self.key_id))


def theporndb_source() -> SceneSource:
    return SceneSource(
        key_id="theporndb", name="ThePornDB",
        search_hash=lambda hashes: theporndb_client.search_by_hash(hashes.oshash),
        search_title=lambda text, year, kind: theporndb_client.search_by_title(text, year or None, kind or "scene"),
        download_cover=lambda url: theporndb_client.download_cover(url),
        kinds=tuple((key, label) for key, (_segment, label) in theporndb_client.KINDS.items()),
    )


def stashdb_source() -> SceneSource:
    return SceneSource(
        key_id="stashdb", name="StashDB",
        search_hash=lambda hashes: stashdb_client.search_by_hash(hashes.oshash, hashes.md5),
        search_title=lambda text, year, kind: stashdb_client.search_by_title(text, year or None),
        download_cover=lambda url: stashdb_client.download_cover(url),
        uses_md5=True,
    )


SOURCE_FACTORIES = {"theporndb": theporndb_source, "stashdb": stashdb_source}


def detail_text(result: SceneResult) -> str:
    """The preview of one result: plain text, never HTML."""
    lines = [result.title or "(untitled)"]
    lines.append("File match: the service has a record with this file's fingerprint." if result.is_file_match
                 else TITLE_WARNING)
    studio = result.studio + (f"  (part of {result.parent_studio})" if result.parent_studio else "")
    for label, value in (
        ("Studio", studio.strip()), ("Date", result.date), ("Director", result.director),
        ("Performers", ", ".join(result.performers)), ("Tags", ", ".join(result.tags)),
        ("Code", result.code), ("Length", f"{result.duration // 60} min {result.duration % 60} s" if result.duration else ""),
        ("On the service", result.page_url), ("Studio page", result.urls[0] if result.urls else ""),
        ("Cover", "available (saved only if you tick the box below)" if result.cover_url else ""),
    ):
        if value:
            lines.append(f"{label}: {value}")
    if result.description:
        lines += ["", result.description]
    return "\n".join(lines)


class SceneSearchDialog(QDialog):
    """Usage: dialog = SceneSearchDialog(source, video_file, parent=window, open_api_keys=window._on_add_external_apis);
    if dialog.exec(): result, save_cover = dialog.selected_result, dialog.save_cover"""

    def __init__(self, source: SceneSource, video: VideoFile, parent=None,
                 open_api_keys: Optional[Callable[[], None]] = None, auto_search: bool = True):
        super().__init__(parent)
        self.source = source
        self.video = video
        self.open_api_keys = open_api_keys
        self.selected_result: Optional[SceneResult] = None
        self.save_cover = False
        self.results: list[SceneResult] = []
        self.status_note = ""
        self.setWindowTitle(f"Search {source.name}")
        self.resize(820, 600)

        layout = QVBoxLayout(self)
        self.privacy_label = QLabel(PRIVACY_NOTE.format(name=source.name))
        self.privacy_label.setWordWrap(True)
        self.privacy_label.setStyleSheet("color: gray;")
        layout.addWidget(self.privacy_label)

        self.key_label = QLabel()
        self.key_label.setWordWrap(True)
        self.key_label.setOpenExternalLinks(True)
        self.key_button = QPushButton("Open Tools > API Keys...")
        self.key_button.clicked.connect(self._on_open_keys)
        layout.addWidget(self.key_label)
        layout.addWidget(self.key_button)

        text, year = search_text_for(video.path.stem)
        search_row = QHBoxLayout()
        self.query_edit = QLineEdit(text)
        self.query_edit.returnPressed.connect(self._on_search)
        search_row.addWidget(self.query_edit, 1)
        self.year_edit = QLineEdit(year)
        self.year_edit.setPlaceholderText("Year")
        self.year_edit.setMaximumWidth(70)
        self.year_edit.returnPressed.connect(self._on_search)
        search_row.addWidget(self.year_edit)
        self.kind_combo: Optional[QComboBox] = None
        if source.kinds:
            self.kind_combo = QComboBox()
            for key, label in source.kinds:
                self.kind_combo.addItem(label, key)
            search_row.addWidget(self.kind_combo)
        self.search_button = QPushButton("Search")
        self.search_button.clicked.connect(self._on_search)
        search_row.addWidget(self.search_button)
        layout.addLayout(search_row)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.table.itemDoubleClicked.connect(lambda _item: self._on_accept())
        layout.addWidget(self.table, 1)

        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMaximumHeight(170)
        layout.addWidget(self.detail)

        self.cover_box = QCheckBox("Save the cover as a sidecar (<name>-poster.jpg)")
        self.cover_box.setToolTip("Downloads the picture only when you apply the result, and never replaces an existing "
                                  "poster without asking.")
        self.cover_box.setEnabled(False)
        layout.addWidget(self.cover_box)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.select_button = QPushButton("Use Selected")
        self.select_button.setEnabled(False)
        self.select_button.clicked.connect(self._on_accept)
        buttons.addWidget(self.select_button)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_button)
        layout.addLayout(buttons)

        make_columns_resizable(self.table, wide_column=COL_TITLE)
        self._refresh_key_state()
        if auto_search and source.has_key():
            self.run_initial_search()

    # --- the key -----------------------------------------------------------------------------------------------

    def _refresh_key_state(self) -> None:
        has_key = self.source.has_key()
        name, url, how = KEY_HELP.get(self.source.key_id, (self.source.name, "", ""))
        if has_key:
            self.key_label.hide()
            self.key_button.hide()
        else:
            link = f'<a href="{url}">{url}</a>' if url else ""
            self.key_label.setText(f"<b>No {name} API key is set.</b> {how} {link}")
            self.key_label.show()
            self.key_button.setVisible(self.open_api_keys is not None)
        self.search_button.setEnabled(has_key)
        self.query_edit.setEnabled(has_key)
        self.year_edit.setEnabled(has_key)
        if self.kind_combo is not None:
            self.kind_combo.setEnabled(has_key)

    def _on_open_keys(self) -> None:
        if self.open_api_keys is not None:
            self.open_api_keys()
        self._refresh_key_state()
        if self.source.has_key() and not self.results:
            self.run_initial_search()

    # --- searching ------------------------------------------------------------------------------------------------

    def run_initial_search(self) -> None:
        """The file's hash first (a "file match"); the title search only when that found nothing."""
        found = self._hash_search()
        if found is None:  # cancelled or failed: the status says which; the user can still press Search
            return
        if found:
            self._show(found)
            self._set_status(f"{len(found)} file match(es): the service has this exact file. Press Search for title results.")
            return
        self._set_status(self.status_note + " Searching by title instead...")
        self._on_search()
        if self.results:
            self._set_status(self.status_note + f" Showing {len(self.results)} title result(s): {TITLE_WARNING}.")
        else:
            self._set_status(self.status_note + " No title result either.")

    def _hash_search(self) -> Optional[list[SceneResult]]:
        """The results by hash, [] for none (and `status_note` says why), None when cancelled or failed."""
        path = str(self.video.path)
        with_md5 = bool(self.source.uses_md5 and md5_enabled())
        cancel = threading.Event()
        label = ("Computing the file's hashes (the MD5 reads the whole file: this can take minutes)..." if with_md5
                 else "Reading the file's fingerprint...")
        try:
            hashes = run_cancellable_lookup(self, file_hashes, path, with_md5, cancel, label=label, cancel_event=cancel)
        except OSError as exc:
            self._set_status(f"The file could not be read for its hash: {exc}")
            return None
        if hashes is CANCELLED or (with_md5 and not hashes.md5 and cancel.is_set()):
            self._set_status("Hashing was cancelled.")
            return None
        if not hashes.oshash:
            self.status_note = "The file is under 128 KB, too small for a hash match."
            return []
        try:
            found = run_cancellable_lookup(self, self.source.search_hash, hashes,
                                           label=f"Asking {self.source.name} about this file...")
        except self.source.errors as exc:
            self._warn(f"{self.source.name} Search Failed", str(exc))
            return None
        if found is CANCELLED:
            self._set_status("The lookup was cancelled.")
            return None
        self.status_note = f"{self.source.name} has no record with this file's fingerprint."
        return found

    def _on_search(self) -> None:
        text = self.query_edit.text().strip()
        if not text or not self.source.has_key():
            return
        year = self.year_edit.text().strip()
        kind = self.kind_combo.currentData() if self.kind_combo is not None else ""
        self._clear()
        try:
            found = run_cancellable_lookup(self, self.source.search_title, text, year, kind,
                                           label=f"Searching {self.source.name}...")
        except self.source.errors as exc:
            self._warn(f"{self.source.name} Search Failed", str(exc))
            return
        if found is CANCELLED:
            self._set_status("The search was cancelled.")
            return
        self._show(found)
        self._set_status("" if found else "No results found.")

    def _warn(self, title: str, message: str) -> None:
        self._set_status(message)
        QMessageBox.warning(self, title, message)

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)

    # --- the results table --------------------------------------------------------------------------------------------

    def _clear(self) -> None:
        self.results = []
        self.table.setRowCount(0)
        self.detail.clear()
        self.select_button.setEnabled(False)
        self.cover_box.setEnabled(False)

    def _show(self, results: list[SceneResult]) -> None:
        self._clear()
        self.results = list(results)
        bold = QFont(self.table.font())
        bold.setBold(True)
        italic = QFont(self.table.font())
        italic.setItalic(True)
        self.table.setRowCount(len(self.results))
        for row, result in enumerate(self.results):
            match_text = FILE_MATCH_LABEL if result.is_file_match else "Title: check it"
            cells = [match_text, result.title or "(untitled)", result.studio, result.date, ", ".join(result.performers)]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setToolTip(TITLE_WARNING if not result.is_file_match else "The service has this exact file.")
                if col == COL_TITLE:
                    item.setData(Qt.ItemDataRole.UserRole, row)
                if result.is_file_match:
                    item.setBackground(QBrush(FILE_MATCH_TINT))
                    item.setFont(bold)
                elif col == COL_MATCH:
                    item.setFont(italic)
                self.table.setItem(row, col, item)
        make_columns_resizable(self.table, wide_column=COL_TITLE)  # nothing is preselected: the pick is explicit

    def _selected(self) -> Optional[SceneResult]:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        index = rows[0].row()
        return self.results[index] if 0 <= index < len(self.results) else None

    def _on_selection_changed(self) -> None:
        result = self._selected()
        self.select_button.setEnabled(result is not None)
        self.cover_box.setEnabled(result is not None and bool(result.cover_url))
        if result is None:
            self.detail.clear()
            self.cover_box.setChecked(False)
            return
        self.detail.setPlainText(detail_text(result))
        if not result.cover_url:
            self.cover_box.setChecked(False)

    def _on_accept(self) -> None:
        result = self._selected()
        if result is None:
            return
        self.selected_result = result
        self.save_cover = bool(self.cover_box.isChecked() and result.cover_url)
        self.accept()
