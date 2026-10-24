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
from redactor_common.gui.remembered_layout import remember_layout, results_detail_splitter
from redactor_common.gui.sortable_table import NaturalTableWidgetItem, enable_sorting, suspend_sorting
from redactor_common.gui.table_columns import make_columns_resizable

from redactor_common.core.source_ids import SourceRef

from core import api_keys, stashdb_client, theporndb_client
from core.config import get_setting, set_setting
from core.source_tags import ref_in
from core.scene_lookup import (
    KEY_HELP, MATCH_ID, TITLE_WARNING, FileHashes, SceneLookupError, SceneResult, file_hashes, md5_enabled,
    folder_note,
    guess_search,
    known_studios_from,
    ladder_note,
    prefer_part_number,
    rank_marks,
    rank_results,
    search_choice_for,
    search_text_for,
    wants_studio_pass,
)
from core.video_file import VideoFile
from gui.lookup import CANCELLED, run_cancellable_lookup
from gui.lookup_file_header import add_bottom_skip_button, add_file_header, file_context
from redactor_common.gui.owned_dialog import OwnedDialog

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
    get_by_id: Optional[Callable[[SourceRef], SceneResult]] = None  # the record of an id the file already carries
    id_kinds: tuple = ("scene",)                    # the kinds of id this service's file tag can hold
    # The structured ladder (ThePornDB): (fields, kind, trace, plain_only) -> (results, step label). None = the service
    # only takes text (StashDB): the single title search with the studio second pass is used.
    search_structured: Optional[Callable[..., tuple]] = None

    def has_key(self) -> bool:
        return bool(api_keys.get_key(self.key_id))


def theporndb_source() -> SceneSource:
    return SceneSource(
        key_id="theporndb", name="ThePornDB",
        search_hash=lambda hashes: theporndb_client.search_by_hash(hashes.oshash),
        search_title=lambda text, year, kind: theporndb_client.search_by_title(text, year or None, kind or "scene"),
        download_cover=lambda url: theporndb_client.download_cover(url),
        kinds=tuple((key, label) for key, (_segment, label) in theporndb_client.KINDS.items()),
        get_by_id=lambda ref: theporndb_client.get(ref.id, ref.kind),
        id_kinds=("scene", "movie", "jav"),
        search_structured=lambda fields, kind, trace, plain_only=False: theporndb_client.search_structured(
            fields, kind or "scene", trace=trace, plain_only=plain_only),
    )


def stashdb_source() -> SceneSource:
    return SceneSource(
        key_id="stashdb", name="StashDB",
        search_hash=lambda hashes: stashdb_client.search_by_hash(hashes.oshash, hashes.md5),
        search_title=lambda text, year, kind: stashdb_client.search_by_title(text, year or None),
        download_cover=lambda url: stashdb_client.download_cover(url),
        uses_md5=True,
        get_by_id=lambda ref: stashdb_client.get(ref.id),
    )


SOURCE_FACTORIES = {"theporndb": theporndb_source, "stashdb": stashdb_source}


def detail_text(result: SceneResult) -> str:
    """The preview of one result: plain text, never HTML."""
    lines = [result.title or "(untitled)"]
    lines.append("File has an ID: the file already carries this record's id." if result.is_id_match
                 else "File match: the service has a record with this file's fingerprint." if result.is_file_match
                 else TITLE_WARNING)
    studio = result.studio + (f"  (part of {result.parent_studio})" if result.parent_studio else "")
    for label, value in (
        ("Studio", studio.strip()), ("Date", result.date), ("Director", result.director),
        ("Performers", ", ".join(result.performers)), ("Tags", ", ".join(result.tags)),
        ("Code", result.code), ("Length", f"{result.duration // 60} min {result.duration % 60} s" if result.duration else ""),
        ("On the service", result.page_url), ("Studio page", result.urls[0] if result.urls else ""),
        ("Poster", "available (embedded in the file only if you tick the box below)" if result.cover_url
         else f"none from {result.source}"),
    ):
        if value:
            lines.append(f"{label}: {value}")
    if result.description:
        lines += ["", result.description]
    return "\n".join(lines)


class SceneSearchDialog(OwnedDialog):
    """Usage: dialog = SceneSearchDialog(source, video_file, parent=window, open_api_keys=window._on_add_external_apis);
    if dialog.exec(): result, save_cover = dialog.selected_result, dialog.save_cover"""

    def __init__(self, source: SceneSource, video: VideoFile, parent=None,
                 open_api_keys: Optional[Callable[[], None]] = None, auto_search: bool = True, context=None):
        super().__init__(parent)
        self.source = source
        self.video = video
        self.open_api_keys = open_api_keys
        self.selected_result: Optional[SceneResult] = None
        self.save_cover = False
        self.accept_landscape = False
        self.file_ref: Optional[SourceRef] = (
            ref_in(video.metadata, source.key_id, source.id_kinds) if source.get_by_id else None)
        self.results: list[SceneResult] = []
        self.status_note = ""
        self.search_line = ""
        self.setWindowTitle(f"Search {source.name}")
        self.resize(820, 600)

        layout = QVBoxLayout(self)
        add_file_header(self, layout, context if context is not None else file_context(video))
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

        self.choice = search_choice_for(video.path)
        # The guess: date/year and studio from the file name, the folders and the file's metadata; a generic file
        # name (clip3) says nothing, so the immediate parent folder's title is the starting point.
        self.known_studios = known_studios_from(getattr(parent, "video_files", None))
        self.guess = guess_search(video.path, video.metadata, self.known_studios)
        text, year = self.guess.text, self.guess.year
        search_row = QHBoxLayout()
        self.query_edit = QLineEdit(text)
        self.query_edit.returnPressed.connect(self._on_search)
        search_row.addWidget(self.query_edit, 1)
        self.year_edit = QLineEdit(year)
        self.year_edit.setPlaceholderText("Year")
        self.year_edit.setMaximumWidth(70)
        self.year_edit.returnPressed.connect(self._on_search)
        search_row.addWidget(self.year_edit)
        self.studio_edit = QLineEdit(self.guess.studio)
        self.studio_edit.setPlaceholderText("Studio")
        self.studio_edit.setToolTip("The studio the file seems to belong to (empty = none). It ranks results from that "
                                    "studio first, and a second search adds it to the text when the title alone finds "
                                    "little. Edit or clear it freely: the next search uses what is here.")
        self.studio_edit.setMaximumWidth(150)
        self.studio_edit.returnPressed.connect(self._on_search)
        search_row.addWidget(self.studio_edit)
        self.kind_combo: Optional[QComboBox] = None
        if source.kinds:
            self.kind_combo = QComboBox()
            for key, label in source.kinds:
                self.kind_combo.addItem(label, key)
            self._restore_kind()
            self.kind_combo.activated.connect(self._remember_kind)  # a deliberate choice only, not a programmatic one
            search_row.addWidget(self.kind_combo)
        self.plain_box: Optional[QCheckBox] = None
        if source.search_structured is not None:
            self.plain_box = QCheckBox("Plain text search")
            self.plain_box.setToolTip("Off: the search asks for the title, then adds the date, the studio and the performers "
                                      "as filters, relaxing them step by step. On: one plain text search, no filters.")
            search_row.addWidget(self.plain_box)
        self.search_button = QPushButton("Search")
        self.search_button.clicked.connect(self._on_search)
        search_row.addWidget(self.search_button)
        self.studio_button = QPushButton("Try with Studio")
        self.studio_button.setToolTip("Search again with the studio's name put in front of the title text.")
        self.studio_button.clicked.connect(self._on_try_studio)
        search_row.addWidget(self.studio_button)
        layout.addLayout(search_row)

        self.folder_box: Optional[QCheckBox] = None
        if self.choice.has_folder:
            self.folder_box = QCheckBox("Search with the folder name")
            self.folder_box.setToolTip(f'The folder is "{self.choice.folder_name}". Checked automatically when the file '
                                       "name says nothing (clip3, scene 2, 001...). Toggling it fills the box again and "
                                       "searches again.")
            self.folder_box.setChecked(self.guess.title_from_folder)
            self.folder_box.toggled.connect(self._on_folder_toggled)
            layout.addWidget(self.folder_box)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        enable_sorting(self.table)  # every header sorts; a row maps to its result through the item's UserRole
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.table.itemDoubleClicked.connect(lambda _item: self._on_accept())

        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.splitter = results_detail_splitter(self.table, self.detail)  # results over details, 60/40, draggable
        layout.addWidget(self.splitter, 1)

        self.cover_box = QCheckBox("Save the poster in the file")
        self.cover_box.setToolTip("Downloads a true poster (portrait, from the service's poster pictures) only when you "
                                  "apply the result, embeds it in the video on Save, and asks before replacing a cover "
                                  "the file already has. No separate picture file is created.")
        self.cover_box.setEnabled(False)
        layout.addWidget(self.cover_box)
        self.landscape_box = QCheckBox("Accept a landscape cover when there is no poster")
        self.landscape_box.setToolTip("Off by default: a landscape picture is not a poster. Tick it to embed the "
                                      "service's wide picture when it has no portrait poster.")
        layout.addWidget(self.landscape_box)
        self._poster_default = get_setting("scene_lookup", "poster_in_file", "0") == "1"
        self._poster_touched = False
        self.cover_box.clicked.connect(lambda: setattr(self, "_poster_touched", True))
        self.landscape_box.toggled.connect(lambda _on: self._on_selection_changed())

        buttons = QHBoxLayout()
        buttons.addStretch()
        add_bottom_skip_button(self, buttons)  # batches only: [Skip This File] [Use Selected] [Cancel]
        self.select_button = QPushButton("Use Selected")
        self.select_button.setEnabled(False)
        self.select_button.clicked.connect(self._on_accept)
        buttons.addWidget(self.select_button)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_button)
        layout.addLayout(buttons)

        make_columns_resizable(self.table, wide_column=COL_TITLE)
        remember_layout(self, "scene_search", get_setting, set_setting, self.splitter)
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
        self.studio_edit.setEnabled(has_key)
        self.studio_button.setEnabled(has_key)
        if self.kind_combo is not None:
            self.kind_combo.setEnabled(has_key)
        if self.plain_box is not None:
            self.plain_box.setEnabled(has_key)

    def _on_open_keys(self) -> None:
        if self.open_api_keys is not None:
            self.open_api_keys()
        self._refresh_key_state()
        if self.source.has_key() and not self.results:
            self.run_initial_search()

    # --- searching ------------------------------------------------------------------------------------------------

    def run_initial_search(self) -> None:
        """The id the file already carries first (no search at all); else the file's hash (a "file match"); the
        title search only when that found nothing."""
        if self.file_ref is not None and self._id_lookup():
            return
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
            self._set_status(f"{self.status_note} {self.search_line} Showing {len(self.results)} title result(s): "
                             f"{TITLE_WARNING}.")
        else:
            self._set_status(f"{self.status_note} {self.search_line} No title result either.")

    def _id_lookup(self) -> bool:
        """Shows the record of the file's own id at the top. False when it could not be fetched (the hash and title
        search then carry on as usual)."""
        ref = self.file_ref
        try:
            result = run_cancellable_lookup(self, self.source.get_by_id, ref,
                                            label=f"Asking {self.source.name} for the id in the file...")
        except self.source.errors as exc:
            self._set_status(f"The id in the file ({ref.id}) could not be fetched: {exc}. Searching instead.")
            return False
        if result is CANCELLED or result is None:
            self._set_status("The lookup was cancelled.")
            return False
        result.match_kind = MATCH_ID
        self._show([result])
        self.table.selectRow(0)
        self._set_status(f"File has an ID: {ref.id}. No search was needed; press Search for other results.")
        return True

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

    def _kind_setting_key(self) -> str:
        return f"kind_{self.source.key_id}"

    def _restore_kind(self) -> None:
        """Movie when the service offers it (the owner's usual target), else what was chosen last time for this
        service: a deliberate switch to Scene sticks."""
        remembered = get_setting("scene_lookup", self._kind_setting_key(), "")
        keys = [key for key, _label in self.source.kinds]
        wanted = remembered if remembered in keys else ("movie" if "movie" in keys else keys[0])
        self.kind_combo.setCurrentIndex(max(self.kind_combo.findData(wanted), 0))

    def _remember_kind(self, _index: int = 0) -> None:
        set_setting("scene_lookup", self._kind_setting_key(), str(self.kind_combo.currentData() or ""))

    def _search_title_pass(self, text: str, year: str, kind: str, label: str):
        return run_cancellable_lookup(self, self.source.search_title, text, year, kind, label=label)

    def _structured_pass(self, text: str, year: str, studio: str, kind: str):
        """The ladder (title + date, title + studio [+ performers], title + year, plain text): (results, status note).
        The boxes as they stand now are the title, year and studio; the date is the guess's, only while the year box
        still holds the guess's year; the performers are the guess's."""
        g = self.guess
        fields = {"title": text, "text": text, "year": year, "studio": studio, "performers": list(g.performers),
                  "date": g.date if (g.date and year == g.year) else ""}
        trace: list = []
        plain = bool(self.plain_box is not None and self.plain_box.isChecked())
        out = run_cancellable_lookup(self, self.source.search_structured, fields, kind, trace, plain,
                                     label=f"Searching {self.source.name}" + (" (plain text)..." if plain else " step by step..."))
        if out is CANCELLED:
            return CANCELLED, ""
        found, label = out
        return found, " " + ladder_note(trace, label, len(found))

    def _on_try_studio(self) -> None:
        self._on_search(force_studio=True)

    def _on_search(self, force_studio: bool = False) -> None:
        """Title-only first; the studio goes into the text only as a second pass (forced by Try with Studio, or when
        the first pass found no file match and few results). Results are ranked by date and studio."""
        text = self.query_edit.text().strip()
        if not text or not self.source.has_key():
            return
        year = self.year_edit.text().strip()
        studio = self.studio_edit.text().strip()
        kind = self.kind_combo.currentData() if self.kind_combo is not None else ""
        self._clear()
        extra = ""
        try:
            if force_studio is True and studio:
                found = self._search_title_pass(f"{studio} {text}", year, kind,
                                                f"Searching {self.source.name} with the studio...")
                extra = " Searched with the studio in the text."
            elif self.source.search_structured is not None:
                found, extra = self._structured_pass(text, year, studio, kind)
            else:
                found = self._search_title_pass(text, year, kind, f"Searching {self.source.name}...")
                if found is not CANCELLED and wants_studio_pass(found, studio, text):
                    second = self._search_title_pass(f"{studio} {text}", year, kind,
                                                     f"Searching {self.source.name} again with the studio...")
                    if second is CANCELLED:
                        found = CANCELLED
                    else:
                        seen = {r.external_id for r in found if r.external_id}
                        found = list(found) + [r for r in second if not (r.external_id and r.external_id in seen)]
                        extra = f" Few results by title alone: searched again with the studio ({studio})."
        except self.source.errors as exc:
            self._warn(f"{self.source.name} Search Failed", str(exc))
            return
        if found is CANCELLED:
            self._set_status("The search was cancelled.")
            return
        if self._using_folder() and self.choice.part_number is not None and found:
            # Several files share this folder (clip1..clip5): the scene whose title carries this file's part number
            # goes first, the others stay below. Never selected for the user.
            order = prefer_part_number([r.title for r in found], self.choice.part_number)
            found = [found[i] for i in order]
        self.rank_date = self.guess.date
        self.rank_studio = studio
        found = rank_results(found, self.rank_date, studio)
        self._show(found)
        line = self._guess_line(text, year, studio)
        self.search_line = line + extra
        self._set_status((line + extra) if found or "Nothing" in extra else (line + extra + " No results found."))

    def _guess_line(self, text: str, year: str, studio: str) -> str:
        """What was searched for and where each guess came from, as the boxes stand now (edited values say so)."""
        g = self.guess
        parts = [f'Searched for: "{text}"']
        if studio:
            parts.append(f"studio from {g.studio_source}: {studio}" if studio == g.studio else f"studio: {studio} (edited)")
        if g.date and year == g.year:
            parts.append(f"date from {g.date_source}: {g.date}")
        elif year:
            parts.append(f"year from {g.date_source}: {year}" if year == g.year else f"year: {year}")
        return " - ".join(parts)

    def _warn(self, title: str, message: str) -> None:
        self._set_status(message)
        QMessageBox.warning(self, title, message)

    def _set_status(self, text: str) -> None:
        note = folder_note(self.choice) if self._using_folder() else ""
        self.status_label.setText((text + " " + note).strip() if note else text)

    def _using_folder(self) -> bool:
        return self.folder_box is not None and self.folder_box.isChecked()

    def _on_folder_toggled(self, on: bool) -> None:
        """Fills the box from the folder name (or back from the file name) and searches again."""
        studio_was_guess = self.studio_edit.text().strip() == self.guess.studio
        self.guess = guess_search(self.video.path, self.video.metadata, self.known_studios, prefer_folder=on)
        self.query_edit.setText(self.guess.text)
        self.year_edit.setText(self.guess.year)
        if studio_was_guess:
            self.studio_edit.setText(self.guess.studio)
        self._on_search()
        if not self.status_label.text():
            self._set_status("")

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
        date, studio = getattr(self, "rank_date", ""), getattr(self, "rank_studio", "")
        with suspend_sorting(self.table):
            self.table.setRowCount(len(self.results))
            for row, result in enumerate(self.results):
                match_text = ("File has an ID" if result.is_id_match
                              else FILE_MATCH_LABEL if result.is_file_match else "Title: check it")
                marks = rank_marks(result, date, studio)
                if marks:
                    match_text += " - " + ", ".join(marks)
                cells = [match_text, result.title or "(untitled)", result.studio, result.date, ", ".join(result.performers)]
                for col, text in enumerate(cells):
                    item = NaturalTableWidgetItem(text)
                    item.setToolTip("The file carries this record's id." if result.is_id_match
                                    else TITLE_WARNING if not result.is_file_match else "The service has this exact file.")
                    item.setData(Qt.ItemDataRole.UserRole, row)  # the result's index: survives any sorting
                    if result.is_file_match or result.is_id_match:
                        item.setBackground(QBrush(FILE_MATCH_TINT))
                        item.setFont(bold)
                    elif col == COL_MATCH:
                        item.setFont(italic)
                    self.table.setItem(row, col, item)
        make_columns_resizable(self.table, wide_column=COL_TITLE)
        # One exact answer (the file's own id or its fingerprint) is preselected so the detail, the poster box and
        # Use Selected are live at once; several rows, or title guesses, stay for the user to pick.
        if len(self.results) == 1 and (self.results[0].is_file_match or self.results[0].is_id_match):
            self.table.selectRow(0)

    def _selected(self) -> Optional[SceneResult]:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        item = self.table.item(rows[0].row(), COL_TITLE)
        index = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return self.results[index] if isinstance(index, int) and 0 <= index < len(self.results) else None

    def _on_selection_changed(self) -> None:
        result = self._selected()
        self.select_button.setEnabled(result is not None)
        has_poster = result is not None and bool(
            result.cover_url or (self.landscape_box.isChecked() and result.landscape_urls))
        self.cover_box.setEnabled(has_poster)
        self.cover_box.setText("Save the poster in the file" if has_poster or result is None else
                               "Save the poster in the file (this result has no portrait poster)")
        if result is None:
            self.detail.clear()
            self.cover_box.setChecked(False)
            return
        self.detail.setPlainText(detail_text(result))
        if not has_poster:
            self.cover_box.setChecked(False)
        elif not self.cover_box.isChecked() and self._poster_default and not self._poster_touched:
            self.cover_box.setChecked(True)

    def _on_accept(self) -> None:
        result = self._selected()
        if result is None:
            return
        self.selected_result = result
        self.accept_landscape = bool(self.landscape_box.isChecked())
        usable = bool(result.cover_url or (self.accept_landscape and result.landscape_urls))
        self.save_cover = bool(self.cover_box.isChecked() and usable)
        set_setting("scene_lookup", "poster_in_file", "1" if self.cover_box.isChecked() else "0")
        if self.save_cover and self.accept_landscape:
            # The user accepted the wide picture for this one: portrait posters first, then the wide ones.
            result.poster_urls = result.poster_urls + [u for u in result.landscape_urls if u not in result.poster_urls]
        self.accept()
