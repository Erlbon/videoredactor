"""
TMDBSearchDialog: candidate picker for TMDB metadata import.

Per explicit instruction: ALWAYS shown, even for a single strong match --
no auto-apply of a "confident" top result. The user picks the specific
right movie/show every time, since a wrong pick here writes wrong
metadata to the actual file (much higher stakes than epub's ISBN lookup
picking a slightly-off cover).

NOTE: not runnable in this sandbox -- no PyQt6, no network. Syntax-checked
and reviewed only.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Optional, Union

from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QVBoxLayout, QHBoxLayout, QListWidget, QListWidgetItem,
    QLineEdit, QPushButton, QLabel, QTextEdit, QMessageBox,
)
from PyQt6.QtCore import Qt

from core.config import get_setting, set_setting
from gui.lookup import run_lookup
from redactor_common.gui.remembered_layout import remember_layout, results_detail_splitter
from gui.lookup_file_header import add_bottom_skip_button, add_file_header
from gui.tmdb_attribution import TmdbAttributionFooter
from core.tmdb_client import (
    search_movies, search_tv, MovieCandidate, TVCandidate, TMDBError,
)

Candidate = Union[MovieCandidate, TVCandidate]


@dataclass
class SearchSource:
    """Another place to search with this same dialog (the local IMDb database,
    TheTVDB): `movies(query, year=None)` and `tv(query, year=None)` return
    MovieCandidate / TVCandidate-like objects (`name`/`title`, `year`, `overview`);
    `errors` are the exception types that mean "the search failed"; `note` is
    shown above the results; `switchable` adds a Film / TV show chooser (the
    dialog's `mode` then follows it); `year_box` False leaves out the Year box."""

    name: str
    movies: Callable
    tv: Callable
    errors: tuple = ()
    note: str = ""
    switchable: bool = False
    year_box: bool = True
    posters: bool = False          # the source has posters: the dialog offers "Save the poster in the file"
    poster_setting: str = ""       # settings section remembering that box ("tmdb_lookup"...); default ON (the old default)


class TMDBSearchDialog(QDialog):
    """Search TMDB and let the user pick exactly one candidate.

    Usage: dialog = TMDBSearchDialog(mode='movie', initial_query=guess, initial_year=year);
    if dialog.exec(): selected = dialog.selected_candidate

    `initial_year` (movie mode only) pre-fills a Year box that narrows
    the search via TMDB's dedicated `year` param -- much more reliable
    than hoping the year survives as plain text inside the query.

    `existing` = (candidate, text): a record the FILE already names by an id it carries (TMDB_ID, TVDB_ID, IMDB_ID).
    It is listed first as "File has an ID: <text>", selected, and no search runs until the user presses Search.
    `selected_how` is then "manual" (else "title"). With a poster-capable source the dialog also has the "Save the
    poster in the file" box (`save_poster`) and "Accept a landscape cover when there is no poster"
    (`accept_landscape`, off by default).

    `context` (gui.lookup_file_header.FileContext): the file being looked up, shown as a header (file x of N, full
    name, what was parsed from it, ids it already has); in a batch it adds Skip This File (this file is left
    untouched, the next one is asked). Cancel, Esc and X cancel the whole lookup in a batch (`stop_requested`) and
    just close the dialog for a single file.
    """

    def __init__(self, mode: str, initial_query: str = "", initial_year: str = "", parent=None,
                 source: Optional[SearchSource] = None, existing: Optional[tuple] = None, context=None):
        super().__init__(parent)
        self.mode = mode  # 'movie' or 'tv'
        self.source = source  # None: TMDB
        self.selected_candidate: Optional[Candidate] = None
        self.selected_how = "title"
        self.save_poster = False
        self.accept_landscape = False
        self._existing = existing
        self._candidates: list[Candidate] = []

        self._set_title()
        self.resize(500, 500)

        layout = QVBoxLayout(self)
        add_file_header(self, layout, context)  # which file this is (gui/lookup_file_header.py)

        if source is not None and source.note:
            note = QLabel(source.note)
            note.setWordWrap(True)
            layout.addWidget(note)

        self.mode_combo: Optional[QComboBox] = None
        if source is not None and source.switchable:
            self.mode_combo = QComboBox()
            self.mode_combo.addItem("Film", "movie")
            self.mode_combo.addItem("TV show", "tv")
            self.mode_combo.setCurrentIndex(self.mode_combo.findData(mode))
            self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
            layout.addWidget(self.mode_combo)

        search_row = QHBoxLayout()
        self.query_edit = QLineEdit(initial_query)
        self.query_edit.returnPressed.connect(self._on_search)
        search_row.addWidget(self.query_edit)

        # Year narrows a movie search a lot (TMDB's /search/movie takes a
        # dedicated `year` param) -- TV search has no equivalent field
        # here, so this box only shows up in movie mode.
        self.year_edit: Optional[QLineEdit] = None
        if mode == "movie" or (source is not None and source.year_box):
            self.year_edit = QLineEdit(initial_year)
            self.year_edit.setPlaceholderText("Year")
            self.year_edit.setMaximumWidth(70)
            self.year_edit.returnPressed.connect(self._on_search)
            search_row.addWidget(self.year_edit)

        self.search_button = QPushButton("Search")
        self.search_button.clicked.connect(self._on_search)
        search_row.addWidget(self.search_button)
        layout.addLayout(search_row)

        self.results_list = QListWidget()
        self.results_list.itemSelectionChanged.connect(self._on_selection_changed)
        self.results_list.itemDoubleClicked.connect(self._on_accept)

        self.overview_label = QTextEdit()
        self.overview_label.setReadOnly(True)
        self.splitter = results_detail_splitter(self.results_list, self.overview_label)
        layout.addWidget(self.splitter, 1)

        self.poster_box: Optional[QCheckBox] = None
        self.landscape_box: Optional[QCheckBox] = None
        if source is None or source.posters:
            self._poster_section = source.poster_setting if source is not None else "tmdb_lookup"
            self.poster_box = QCheckBox("Save the poster in the file")
            self.poster_box.setToolTip(
                "Downloads the service's poster (a portrait picture; never a frame from the video) when you use the "
                "result, embeds it in the video on Save, and asks before replacing a cover the file already has. "
                "No separate picture file is created.")
            self.poster_box.setChecked(get_setting(self._poster_section, "poster_in_file", "1") == "1")
            layout.addWidget(self.poster_box)
            self.landscape_box = QCheckBox("Accept a landscape cover when there is no poster")
            self.landscape_box.setToolTip("Off by default: a landscape picture is not a poster.")
            layout.addWidget(self.landscape_box)

        # TMDB's required notice and logo; not for the other sources this
        # dialog also serves (the local IMDb database has its own).
        if source is None:
            self.attribution = TmdbAttributionFooter(self)
            layout.addWidget(self.attribution)

        button_row = QHBoxLayout()
        button_row.addStretch()
        add_bottom_skip_button(self, button_row)  # batches only: [Skip This File] [Use Selected] [Cancel]
        self.select_button = QPushButton("Use Selected")
        self.select_button.setEnabled(False)
        self.select_button.clicked.connect(self._on_accept)
        button_row.addWidget(self.select_button)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        button_row.addWidget(self.cancel_button)
        layout.addLayout(button_row)
        remember_layout(self, type(self).__name__.lower(), get_setting, set_setting, self.splitter)

        if existing is not None:
            self._show_existing()
        elif initial_query:
            self._on_search()

    def _show_existing(self) -> None:
        candidate, text = self._existing
        item = QListWidgetItem(f"File has an ID: {text}")
        item.setData(Qt.ItemDataRole.UserRole, candidate)
        font = item.font()
        font.setBold(True)
        item.setFont(font)
        self.results_list.addItem(item)
        self.results_list.setCurrentRow(0)

    def _set_title(self) -> None:
        kind = "Movie" if self.mode == "movie" else "TV Show"
        self.setWindowTitle(f"Search {self.source.name} ({kind})" if self.source else f"Search TMDB ({kind})")

    def _on_mode_changed(self) -> None:
        self.mode = self.mode_combo.currentData()
        self._set_title()
        self._on_search()

    def _on_search(self) -> None:
        query = self.query_edit.text().strip()
        if not query:
            return

        self.results_list.clear()
        self.overview_label.clear()
        self.select_button.setEnabled(False)
        if self._existing is not None:
            self._show_existing()

        errors = (TMDBError,) + (self.source.errors if self.source else ())
        try:
            year = self.year_edit.text().strip() if self.year_edit else ""
            if self.source is not None:
                search = self.source.movies if self.mode == "movie" else self.source.tv
                self._candidates = run_lookup(self, search, query, year=year or None)
            elif self.mode == "movie":
                self._candidates = run_lookup(self, search_movies, query, year=year or None)
            else:
                self._candidates = run_lookup(self, search_tv, query)
        except errors as e:
            QMessageBox.warning(self, f"{self.source.name if self.source else 'TMDB'} Search Failed", str(e))
            self._candidates = []
            return

        if not self._candidates:
            self.results_list.addItem("No results found")
            return

        for candidate in self._candidates:
            label = self._candidate_label(candidate)
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, candidate)
            self.results_list.addItem(item)

    def _candidate_label(self, candidate: Candidate) -> str:
        year_part = f" ({candidate.year})" if candidate.year else ""
        alias = getattr(candidate, "alias", "")
        alias_part = f" - also known as '{alias}'" if alias else ""
        if isinstance(candidate, MovieCandidate):
            return f"{candidate.title}{year_part}{alias_part}"
        return f"{candidate.name}{year_part}{alias_part}"

    def _on_selection_changed(self) -> None:
        items = self.results_list.selectedItems()
        if not items:
            self.select_button.setEnabled(False)
            self.overview_label.clear()
            return
        candidate = items[0].data(Qt.ItemDataRole.UserRole)
        if candidate is None:  # the "No results found" placeholder item
            self.select_button.setEnabled(False)
            return
        self.select_button.setEnabled(True)
        self.overview_label.setPlainText(candidate.overview or "(no synopsis available)")

    def _on_accept(self) -> None:
        items = self.results_list.selectedItems()
        if not items:
            return
        candidate = items[0].data(Qt.ItemDataRole.UserRole)
        if candidate is None:
            return
        self.selected_candidate = candidate
        self.selected_how = "manual" if self._existing is not None and candidate is self._existing[0] else "title"
        if self.poster_box is not None:
            self.save_poster = self.poster_box.isChecked()
            self.accept_landscape = bool(self.landscape_box and self.landscape_box.isChecked())
            set_setting(self._poster_section, "poster_in_file", "1" if self.save_poster else "0")
        self.accept()
