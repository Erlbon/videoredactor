"""
EpisodePickerDialog: pick a specific season + episode of a matched TV show,
so episode-level fields (episode title, season #, episode #, air date) can
be applied on top of the show-level import.

One dialog for every source (TMDB, TheTVDB, the local IMDb database); what
differs lives in an EpisodeSource:
- TMDB fetches each season's episodes with a new request when the season
  changes (gui/tmdb_episode_picker_dialog.py);
- TheTVDB has no per-season endpoint, so it fetches the whole episode list
  once at open and filters it in memory (gui/tvdb_episode_picker_dialog.py);
- IMDb reads the offline database (gui/imdb_episode_picker_dialog.py).

"Always confirm explicitly, never auto-pick": guessing the wrong episode
writes wrong metadata just as badly as guessing the wrong show.
`initial_season`/`initial_episode`, when given (typically parsed from the
file's own name via core.release_name_parser), only PRE-SELECT that season
and episode -- the user still has to click "Use Selected Episode" (or
double-click) for anything to happen.

Usage: dialog = EpisodePickerDialog(source, parent=self);
if dialog.exec(): season, episode = dialog.selected_season, dialog.selected_episode
"""

from __future__ import annotations

from typing import Any, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from gui.lookup_file_header import add_file_header
from core.config import get_setting, set_setting
from redactor_common.gui.remembered_layout import remember_layout, results_detail_splitter
from redactor_common.gui.owned_dialog import OwnedDialog


CHOICE_EPISODE = "episode"
CHOICE_SHOW_ONLY = "show_only"
CHOICE_SKIP = "skip"


def picker_choice(dialog, accepted: bool) -> str:
    """The decision of a picker dialog as one of the CHOICE_* words. Works for the real dialogs and for simple stand-ins:
    a dialog that was not accepted is a skip; one without a `choice` that picked an episode is "episode"."""
    if not accepted:
        return CHOICE_SKIP
    choice = getattr(dialog, "choice", None)
    if choice in (CHOICE_EPISODE, CHOICE_SHOW_ONLY, CHOICE_SKIP):
        return choice
    return CHOICE_EPISODE if getattr(dialog, "selected_episode", None) is not None else CHOICE_SKIP


class EpisodeSource:
    """Where a picker's seasons and episodes come from, plus the texts that differ per source.

    `seasons(owner)` returns [(season number, combo label)] and `episodes(owner, season)` the
    season's EpisodeInfo-like objects (`episode_number`, `name`, `overview`), in display order.
    `owner` is the dialog, for lookups that run under gui.lookup.run_lookup's busy dialog. Both
    raise one of `errors` when the lookup fails; the dialog shows that, it never raises it."""

    window_title = "Select Season & Episode"
    heading = ""  # shown in bold above the season combo when set
    errors: tuple = ()
    seasons_error_title = "Could Not Load Seasons"
    empty_title = "No Seasons Found"
    empty_text = "This show has no listed seasons."
    no_overview = "(no synopsis available)"

    def seasons(self, owner: QWidget) -> list[tuple[int, str]]:
        raise NotImplementedError

    def episodes(self, owner: QWidget, season: int) -> list[Any]:
        raise NotImplementedError

    def footer(self, parent: QWidget) -> Optional[QWidget]:
        """A widget under the detail box (TMDB's required attribution), or None."""
        return None


def episode_label(ep) -> str:
    return f"E{ep.episode_number}: {ep.name}" if ep.name else f"Episode {ep.episode_number}"


class EpisodePickerDialog(OwnedDialog):
    def __init__(
        self, source: EpisodeSource, parent=None, initial_season: Optional[int] = None,
        initial_episode: Optional[int] = None, title: Optional[str] = None, context=None,
    ):
        super().__init__(parent)
        self.source = source
        self._initial_season = initial_season
        self._initial_episode = initial_episode
        self.selected_season: Optional[int] = None
        self.selected_episode = None
        # What the user decided for THIS file: "episode" (use the picked episode), "show_only" (the show's
        # information but no season/episode data) or "skip" (the file stays completely untouched). Closing the
        # window (Esc, X, Skip This File) is "skip", never "show_only".
        self.choice = CHOICE_SKIP

        self.setWindowTitle(title or source.window_title)
        self.resize(450, 500)

        layout = QVBoxLayout(self)
        add_file_header(self, layout, context)
        if source.heading:
            layout.addWidget(QLabel(f"<b>{source.heading}</b>"))

        layout.addWidget(QLabel("Season:"))
        self.season_combo = QComboBox()
        self.season_combo.currentIndexChanged.connect(self._on_season_changed)
        layout.addWidget(self.season_combo)

        layout.addWidget(QLabel("Episode:"))
        self.episode_list = QListWidget()
        self.episode_list.itemSelectionChanged.connect(self._on_episode_selection_changed)
        self.episode_list.itemDoubleClicked.connect(self._on_accept)

        self.overview_box = QTextEdit()
        self.overview_box.setReadOnly(True)
        self.splitter = results_detail_splitter(self.episode_list, self.overview_box)
        layout.addWidget(self.splitter, 1)

        footer = source.footer(self)
        if footer is not None:
            self.attribution = footer
            layout.addWidget(footer)

        button_row = QHBoxLayout()
        button_row.addStretch()
        self.select_button = QPushButton("Use This Episode")
        self.select_button.setToolTip(
            "The file gets the show's information, the poster and ids, and this episode's details")
        self.select_button.setEnabled(False)
        self.select_button.clicked.connect(self._on_accept)
        button_row.addWidget(self.select_button)
        self.show_only_button = QPushButton("Show Information Only")
        self.show_only_button.setToolTip(
            "For extras, 'Making of' or specials that are not in the episode list: the show's information, poster "
            "and show id, but no season, episode, episode title or episode description")
        self.show_only_button.clicked.connect(self._on_show_only)
        button_row.addWidget(self.show_only_button)
        batch = bool(self.file_header is not None and self.file_header.context.is_batch)
        if batch:
            # Four buttons in a batch: Use This Episode / Show Information Only / Skip This File (this file stays
            # untouched, the next is asked) / Cancel Lookup (Esc and the window's X too: this file and the rest stay
            # untouched, the files confirmed earlier keep their results).
            self.skip_button = QPushButton("Skip This File")
            self.skip_button.setToolTip("This file stays completely untouched; the next file is asked")
            self.skip_button.clicked.connect(lambda: self.skip_file())
            button_row.addWidget(self.skip_button)
        self.cancel_button = QPushButton("Cancel Lookup" if batch else "Cancel")
        self.cancel_button.setToolTip(
            "End the lookup here: this file and every file after it stay untouched (Esc does the same)" if batch
            else "Close without choosing anything (Esc does the same)")
        self.cancel_button.clicked.connect(self.reject)
        button_row.addWidget(self.cancel_button)
        layout.addLayout(button_row)
        remember_layout(self, type(self).__name__.lower(), get_setting, set_setting, self.splitter)

        self._load_seasons()

    def _load_seasons(self) -> None:
        try:
            seasons = self.source.seasons(self)
        except self.source.errors as e:
            QMessageBox.warning(self, self.source.seasons_error_title, str(e))
            self.skip_file()
            return

        if not seasons:
            QMessageBox.information(self, self.source.empty_title, self.source.empty_text)
            self.skip_file()
            return

        for number, label in seasons:
            self.season_combo.addItem(label, number)
        # currentIndexChanged fires from addItem above once a default
        # selection exists, loading episodes for the first season.

        if self._initial_season is not None:
            idx = self.season_combo.findData(self._initial_season)
            if idx >= 0:
                self.season_combo.setCurrentIndex(idx)  # re-fires _on_season_changed if it actually moved

    def _on_season_changed(self) -> None:
        season_number = self.season_combo.currentData()
        if season_number is None:
            return
        self.episode_list.clear()
        self.overview_box.clear()
        self.select_button.setEnabled(False)

        try:
            episodes = self.source.episodes(self, season_number)
        except self.source.errors as e:
            QMessageBox.warning(self, "Could Not Load Episodes", str(e))
            return

        select_row = -1
        for row, ep in enumerate(episodes):
            item = QListWidgetItem(episode_label(ep))
            item.setData(Qt.ItemDataRole.UserRole, ep)
            self.episode_list.addItem(item)
            if (
                self._initial_episode is not None
                and season_number == self._initial_season
                and ep.episode_number == self._initial_episode
            ):
                select_row = row

        # Pre-select the episode parsed from the filename, if this is that
        # season -- still just a selection, not a confirmed pick.
        if select_row >= 0:
            self.episode_list.setCurrentRow(select_row)

    def _on_episode_selection_changed(self) -> None:
        items = self.episode_list.selectedItems()
        if not items:
            self.select_button.setEnabled(False)
            self.overview_box.clear()
            return
        episode = items[0].data(Qt.ItemDataRole.UserRole)
        self.select_button.setEnabled(True)
        self.overview_box.setPlainText(episode.overview or self.source.no_overview)

    def _on_accept(self) -> None:
        items = self.episode_list.selectedItems()
        if not items:
            return
        self.selected_season = self.season_combo.currentData()
        self.selected_episode = items[0].data(Qt.ItemDataRole.UserRole)
        self.choice = CHOICE_EPISODE
        self.accept()

    def _on_show_only(self) -> None:
        self.selected_season = None
        self.selected_episode = None
        self.choice = CHOICE_SHOW_ONLY
        self.accept()

    def reject(self) -> None:  # the window's closing paths (Skip, and in a batch Cancel/Esc/X): nothing is applied
        self.choice = CHOICE_SKIP
        self.selected_season = None
        self.selected_episode = None
        super().reject()
