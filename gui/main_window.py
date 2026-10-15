"""
MainWindow: The Ʌideo Redactor's central shell.

Layout mirrors the epub tool: bulk-edit panel (left, via TagPanel) +
sortable file table (right). Row-to-file mapping uses Qt.UserRole storing
a VideoFile reference, not row index -- same reasoning as the epub tool:
index-based mapping breaks under sorting/reordering, UserRole doesn't.

Content Type filter (dropdown above the table) drives BOTH which table
columns are visible AND which fields TagPanel shows -- "no filter" means
"show everything," per explicit instruction.
"""

from __future__ import annotations
from pathlib import Path
from typing import Callable, Optional
import copy
import json
import os
import shutil
import threading

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QSplitter,
    QTableWidget, QTableWidgetItem, QComboBox, QLabel, QFileDialog,
    QStatusBar, QMessageBox, QToolBar, QCheckBox,
)
from PyQt6.QtCore import Qt, QItemSelection, QItemSelectionModel, QSize
from PyQt6.QtGui import QColor, QImage, QKeySequence, QIcon

from core.video_file import (
    SUPPORTED_EXTENSIONS, VideoFile, discover_convertible_files, discover_video_files, has_subfolders,
)
from core.video_metadata import ContentType, EDITABLE_FIELDS, NUMERIC_FIELDS, TEXT_FIELDS
from core.filename_pattern import (
    DEFAULT_RENAME_PATTERN, PARSE_NUMERIC_FIELDS, PARSE_STRIP_ZEROS_FIELDS, VALID_FIELD_KEYS,
    field_text, load_pattern_history, placeholder_values, save_pattern_to_history, set_field_text,
)
from core.tmdb_client import (
    get_movie_details, get_tv_show_details, get_tv_episode_details,
    download_poster, TMDBError,
)
from core.tvdb_client import get_series_details, get_episode_details, download_image, TVDBError
from core.release_name_parser import parse_release_name
from core.redact_steps import _tv_guess, conversion_is_series
from core.sidecars import with_sidecars
from core.redact_steps import (
    RedactEnv, VideoCtx, build_catalogue, dry_run_line, finalize_file, load_recipe, make_safe_run,
    original_fate_text, pin_patterns, recipe_is_saved, save_recipe,
)
from core import conversion_plan, conversion_run, history, library_safety
from core.ffmpeg_backend import (
    IMPORTABLE_EXTENSIONS, kill_running_ffmpeg, remux_to_mkv, remux_to_mp4,
    transcode_to_mp4, verify_remux,
)
from core.settings_adapter import REFRESH_COLUMNS, REFRESH_PANEL, VideoSettingsAdapter
from core.transcode_settings import get_transcode_settings
from core.opensubtitles_client import download_subtitle_text, OpenSubtitlesError
from core.table_settings import PROTECTED_COLUMNS, merge_column_order, is_column_visible, sanitize_hidden_fields
from core.format_helpers import bitrate_bps, format_bitrate, format_duration, format_file_size
from core.config import get_setting, set_setting
from redactor_common.gui.action_factory import make_action
from redactor_common.core import labels
from redactor_common.gui.command_palette import add_command_palette
from redactor_common.gui.menu_builder import MenuAction, MenuItems, Separator, Submenu
from redactor_common.gui.settings_bundle_dialogs import export_settings, import_settings
from redactor_common.gui.standard_menus import (
    AppMenu, StandardMenuSpec, build_standard_menu_bar, get_action_registry, look_up_submenu,
    set_apply_count, standard_edit_items, standard_file_items, standard_help_items,
    standard_tools_items, standard_view_items, with_aliases,
)
from redactor_common.gui.async_preview import AsyncPreviewLoader
from redactor_common.gui.background_call import BackgroundCancelled, call_in_background
from redactor_common.gui.auto_numbering_dialog import AutoNumberingDialog
from redactor_common.gui.case_conversion_dialog import CaseConversionDialog
from redactor_common.gui.parse_filename_dialog import ParseFilenameDialog
from redactor_common.core.rename_log import RenameLog
from redactor_common.gui.rename_undo import undo_last_rename
from core.app_paths import base_dir
from redactor_common.gui.rename_pattern_dialog import RenamePatternDialog
from redactor_common.gui.search_replace_dialog import FILENAME_FIELD_KEY, SearchReplaceDialog
from redactor_common.gui.move_runner import run_planned_moves
from redactor_common.gui.folder_options import (
    add_include_subfolders_checkbox, attach_include_subfolders, include_subfolders_slot,
)
from redactor_common.gui.table_columns import make_columns_resizable
from redactor_common.gui.column_filter import KIND_TEXT, KIND_VALUES, ColumnFilterHeader
from redactor_common.gui.progress import run_busy, run_with_progress
from redactor_common.gui.work_progress import run_work_with_progress
from redactor_common.gui.convert_offer import YES as CONVERT_YES, offer_conversion
from redactor_common.gui.history_dialog import HistoryDialog
from redactor_common.gui.redact_dialog import RecipeEditorDialog, RedactResultsDialog
from redactor_common.gui.redact_dialog import run_redact as run_redact_dialog
from redactor_common.gui.colors import (
    DIRTY_COLOR, ERROR_COLOR, HIGHLIGHT_TEXT_COLOR, SAVE_FAILED_COLOR, TABLE_SELECTION_STYLESHEET,
)
from redactor_common.gui.context_menu import show_table_context_menu
from redactor_common.gui.quick_series_number import prompt_and_generate_series_numbers
from redactor_common.gui.column_menu import show_column_header_context_menu
from redactor_common.gui.column_settings_dialog import ColumnSettingsDialog
from redactor_common.gui.collapsible_splitter import SplitterPaneCollapser
from redactor_common.gui.zoom_toolbar import TableZoomController
from redactor_common.gui.about_dialog import AboutDialog, ChangelogDialog
from redactor_common.gui.rename_single_file import rename_single_file as prompt_rename_single_file
from redactor_common.gui.sortable_table import NumericTableWidgetItem, suspend_sorting
from redactor_common.gui import standard_shortcuts as shortcuts
from redactor_common.core.error_summary import summarize_errors
from redactor_common.core.trash import TrashError, move_to_trash
from redactor_common.core.os_utils import rename_no_clobber
from redactor_common.core.undo import UndoManager
from redactor_common.core.folder_refresh import find_new_files_in_loaded_folders
from redactor_common.core.version import REDACTOR_COMMON_REPO_URL, REDACTOR_COMMON_VERSION
from gui.lookup import run_lookup
from gui.tag_panel import TagPanel, FIELD_LABELS
from gui.tmdb_search_dialog import SearchSource, TMDBSearchDialog
from gui.tmdb_episode_picker_dialog import TVEpisodePickerDialog
from gui.tmdb_attribution import AppCreditsDialog
from gui.tvdb_search_dialog import TVDBSearchDialog
from gui.tvdb_episode_picker_dialog import TVDBEpisodePickerDialog
from gui.subtitle_search_dialog import SOURCE_LRCLIB, SubtitleSearchDialog
from gui.subtitle_translation_dialog import MODE_COMPARE, MODE_TRANSLATE, SubtitleTranslationDialog
from gui.tool_settings_dialog import ToolSettingsDialog
from gui.video_preferences import VideoPreferencesDialog
from redactor_common.gui.preferences_dialog import preferences_menu_action
from gui.vocabulary_editor_dialog import VocabularyEditorDialog
from gui.api_keys_dialog import ApiKeysDialog
from core import imdb_settings
from core.controlled_vocab import (
    get_genre_options, add_genre_option, remove_genre_option,
    get_language_options, add_language_option, remove_language_option,
)
from core.version import APP_NAME, APP_REPO_URL, APP_VERSION, RELEASE_LABEL
from core.external_tools import missing_tools
from core.app_paths import asset_path

# Bundled data assets (icon, ABOUT/CHANGELOG/CREDITS markdown) resolve
# via sys._MEIPASS when frozen -- see core/app_paths.asset_path().
ASSETS_DIR = asset_path("assets")
PROJECT_ROOT = asset_path(".")
ICON_PATH = ASSETS_DIR / "icon.ico"
CHANGELOG_PATH = PROJECT_ROOT / "CHANGELOG.md"
ABOUT_PATH = PROJECT_ROOT / "ABOUT.md"
CREDITS_PATH = PROJECT_ROOT / "CREDITS.md"
# The menu's own words for getting files into the list (File > Open Files / Open Folder).
OPEN_FILES_FIRST = "Open some video files first (File > Open Files or Open Folder)."

# Columns always shown regardless of filter (Filename/Status are
# structural, not metadata fields, so the content-type filter never
# hides them).
ALWAYS_VISIBLE_COLUMNS = ["filename", "status", "content_type"]

# Read-only technical columns -- populated via ffprobe (VideoFile.load())
# or a live filesystem stat (size_bytes), NOT part of VideoMetadata's
# EDITABLE_FIELDS, so these never appear in TagPanel's bulk-edit form --
# table-only, informational. Shown regardless of Content Type filter
# (unlike Director/Cast/etc, technical info isn't content-type-specific)
# but still user-hideable via the column context menu like anything else.
TECHNICAL_COLUMNS = ["path", "duration_seconds", "resolution", "size_bytes", "bitrate"]
TECHNICAL_LABELS = {
    "path": "Path",
    "duration_seconds": "Duration",
    "resolution": "Resolution",
    "size_bytes": "Size",
    "bitrate": "Bitrate",
}

# Header filters (the funnel in a column header): which columns get one. KIND_VALUES = a checklist of the
# distinct values; KIND_TEXT = a "contains..." box above the checklist. Left out on purpose: Duration, Size and
# Bitrate (almost every value is distinct, so a checklist would be useless).
FILTER_COLUMN_KINDS = {
    "filename": KIND_TEXT, "path": KIND_TEXT, "status": KIND_VALUES, "content_type": KIND_VALUES,
    "resolution": KIND_VALUES, "language": KIND_VALUES,
    "personal_rating": KIND_VALUES, "season_number": KIND_VALUES, "episode_number": KIND_VALUES,
    **{f: KIND_TEXT for f in (
        "title", "sort_title", "description", "genre_tags", "release_date", "comment", "director", "cast",
        "writer", "studio", "collection", "show_title", "network", "artist", "album", "track_title", "composer",
    )},
}

FILE_ROLE = Qt.ItemDataRole.UserRole  # row -> VideoFile mapping, not row index
TAG_PANEL_COLLAPSED_WIDTH = 32  # slim strip, not zero -- keeps the panel's own toggle button reachable

# Single source of truth for column header labels -- built once at
# module load rather than reconstructed inline at each use site, so
# there's no risk of the two sites (column rebuild, context menu) ever
# drifting out of sync the way the epub tool's v45 fix once did when a
# shared mechanism was updated in only one of its sibling call sites.
COLUMN_LABEL_LOOKUP = {**FIELD_LABELS, **TECHNICAL_LABELS, "filename": "Filename", "status": "Status"}

# Status-column highlight colors. Each row color is a deliberately
# matched (background, foreground) PAIR rather than background-only --
# an earlier version of this code set background alone, reasoning
# (incorrectly, as it turns out) that leaving foreground untouched
# would let the theme's own text color always contrast correctly. That
# held for a light theme's dark default text, but broke exactly the
# same way in the other direction under a dark theme: a light pastel
# background with the theme's light/white default text is just as
# unreadable as the epub tool's original dark-mode bug (accidentally
# black text on a dark background) -- same underlying mistake, opposite
# color. The actual fix is to never depend on the theme's default text
# color for a background WE chose: own both colors as a fixed, always-
# readable pair, so these cells are contrast-correct regardless of
# which theme (or theme-following mode) the user is running.
# Colors now live in redactor_common.gui.colors -- the epub tool's
# scheme became the shared standard across all three projects (each
# had independently picked its own row-tint/selection colors before).
# Note for posterity: this project's own SELECTION_BG ("#14327D", a
# dark navy) was chosen and verified at 11.6:1 contrast specifically
# for dark-theme readability -- the shared "#2f6fed" hasn't been
# re-verified against a dark theme here. Flagged, not blocking, per
# explicit direction to standardize on epub's scheme regardless.


# Rename/Export and Parse Filename placeholders: every editable field,
# labelled the same way as the bulk-edit panel.
FILENAME_PLACEHOLDERS = [(field, FIELD_LABELS.get(field, field)) for field in EDITABLE_FIELDS]



def _rename_log_at(path: str) -> RenameLog:
    """A RenameLog whose renames, moves and undos are passed on to Tools > History (history.record_moves:
    a file's journal entries follow it; a no-op while history is off)."""
    return RenameLog(path, on_move=history.record_moves)


def _rename_log() -> RenameLog:
    """The persistent log behind File > Undo Last Rename (redactor_common's
    core/rename_log.py), next to this app's settings."""
    return _rename_log_at(os.path.join(str(base_dir()), "videoredactor_rename_log.json"))

def _field_text(vf: VideoFile, field_name: str) -> str:
    """A metadata field as the plain string the shared dialogs work on."""
    return field_text(vf.metadata, field_name)


def _set_field_from_text(vf: VideoFile, field_name: str, text: str) -> bool:
    """Stores a string from a shared dialog back into its typed field
    (see core.filename_pattern.set_field_text). Returns False (and leaves
    the field untouched) for a value that doesn't fit: skipping one field
    beats crashing the whole batch or writing a string into an int field."""
    return set_field_text(vf.metadata, field_name, text)


def _error_details(lines: list[str]) -> str:
    """A bounded "N file(s): a; b; c, ..." message-box body -- ffmpeg's
    stderr alone can run to hundreds of lines per file, and a failed
    batch of hundreds of files used to produce a message box taller
    than the screen, with its OK button out of reach."""
    return f"{len(lines)} file(s):\n\n{summarize_errors(lines)}"


def _journal_problem_note() -> str:
    """A Tools > History journal problem not shown yet, as a status-bar suffix ("" when none): a journal
    failure never fails a save, it is surfaced once here."""
    problem = history.take_problem()
    return f" -- {problem}" if problem else ""


def _run_transcode_batch(
    jobs: list[tuple[Path, Path]], settings, cancel_event: threading.Event,
    remux: bool = False, mkv: bool = False,
    on_started: Optional[Callable[[int, str], None]] = None,
) -> dict[int, tuple[bool, str]]:
    """Runs a batch of transcode_to_mp4() (or, with remux=True, remux_to_mp4(); with mkv=True,
    remux_to_mkv()) calls and returns {job index: (ok, message)}; a job not started because
    `cancel_event` was set first is missing from the result. Runs on a worker thread
    (_run_transcode_jobs): no widgets here.

    `cancel_event` is checked between files here and (via the ffmpeg backend) mid-file, so Cancel
    stops a long encode instead of only skipping the files not yet started; the backend removes
    the partial output.

    The message: for a transcode, ffmpeg's stderr; for a remux that succeeded, verify_remux()'s
    verdict ("" = every track made it, anything else = what's missing) -- on failure, the error.
    """
    results: dict[int, tuple[bool, str]] = {}
    for i, (input_path, output_path) in enumerate(jobs):
        if cancel_event.is_set():
            break
        if on_started is not None:
            on_started(i, input_path.name)
        # A PermissionError, a vanished file... must not end the batch:
        # the remaining jobs would then look "cancelled".
        try:
            if remux or mkv:
                remux_fn = remux_to_mkv if mkv else remux_to_mp4
                ok, message = remux_fn(str(input_path), str(output_path), cancel_event=cancel_event)
                if ok:
                    message = verify_remux(str(input_path), str(output_path))
            else:
                ok, message = transcode_to_mp4(
                    str(input_path), str(output_path),
                    crf=settings.crf,
                    audio_bitrate=settings.audio_bitrate,
                    threads=settings.threads or None,
                    cancel_event=cancel_event,
                )
        except Exception as exc:  # noqa: BLE001 -- reported per file
            ok, message = False, str(exc) or type(exc).__name__
        results[i] = (ok, message)
    return results


def _load_include_subfolders() -> bool:
    """File > Include Subfolders: ON unless turned off (the shared setting every Redactor app has)."""
    return get_setting("general", "include_subfolders", "1") != "0"


def _save_include_subfolders(on: bool) -> None:
    set_setting("general", "include_subfolders", "1" if on else "0")


def _claim_output(output: Path, claimed: set[str]) -> bool:
    """True (and remembers it in `claimed`) when `output` is free: not
    on disk and not already the destination of an earlier job in this
    batch -- a.avi and a.mov would both become a.mp4 and overwrite each
    other."""
    key = os.path.normcase(os.path.abspath(output))
    if output.exists() or key in claimed:
        return False
    claimed.add(key)
    return True


def _copy_no_clobber(src: str, dst: str) -> None:
    """shutil.copy2 that refuses to replace an existing `dst` (copy2
    silently overwrites, and "Export" must never destroy a file): copies
    to a temporary name, then renames without clobbering."""
    if os.path.exists(dst):
        raise FileExistsError(f"{os.path.basename(dst)} already exists")
    tmp = f"{dst}.copying"
    try:
        shutil.copy2(src, tmp)
        rename_no_clobber(tmp, dst)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _drop_keys(items: MenuItems, keys: set[str]) -> MenuItems:
    """Copies `items` without the actions whose key is in `keys`."""
    return [i for i in items if not (isinstance(i, MenuAction) and i.key in keys)]


def _tidy(items: MenuItems) -> MenuItems:
    """Removes leading, trailing and doubled separators left by _drop_keys."""
    out: MenuItems = []
    for item in items:
        if isinstance(item, Separator) and (not out or isinstance(out[-1], Separator)):
            continue
        out.append(item)
    while out and isinstance(out[-1], Separator):
        out.pop()
    return out


def _with_preferences(slot, items: MenuItems) -> MenuItems:
    """Preferences (Ctrl+, with the platform Preferences role) heads the Tools
    menu, in a group of its own."""
    return [preferences_menu_action(slot), Separator(), *items]


def _remember_zero_pad(enabled: bool, width: int) -> None:
    set_setting("rename", "zero_pad", "1" if enabled else "0")
    set_setting("rename", "zero_pad_width", str(width))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} ({APP_VERSION})")
        self.resize(1200, 700)

        if ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(ICON_PATH)))
        # No else/warning here -- a missing icon file shouldn't block the
        # app from launching; Qt just falls back to no icon silently,
        # which is the right degrade for something this cosmetic.

        self.video_files: list[VideoFile] = []
        # In-memory edits (bulk edit, lookups, batch text operations) are
        # undoable, same as epub/mp3/cbz -- this project had no undo at
        # all before 2026-09-23. File operations on disk (rename, remux,
        # convert) are not, matching the other apps.
        self.undo_manager: UndoManager[VideoFile] = UndoManager()
        self._column_order: list[str] = []  # populated in _rebuild_table_columns
        self._suppress_column_signals = False  # True while _rebuild_table_columns
        # is programmatically applying persisted widths/visibility, so those
        # calls don't get misread as user actions and re-saved redundantly
        # (or, for a hidden column's width momentarily reporting as 0,
        # incorrectly overwrite a perfectly good persisted width).

        # Thumbnails are generated and decoded off the GUI thread; see
        # _update_preview(). Decoded no larger than this -- ffmpeg grabs
        # the frame at the video's full resolution.
        self._preview_loader = AsyncPreviewLoader(QSize(960, 960), parent=self)
        self._preview_loader.image_ready.connect(self._on_preview_ready)

        self._build_ui()
        self._build_menu_bar()
        self._build_toolbar()
        self._rebuild_table_columns()
        self._check_external_tools_on_startup()
        self._restore_last_folder_on_startup()

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)

        # (No filter row above the table: every filterable column has a funnel in its header, see
        # ColumnFilterHeader below; Open Folder / Save live on the toolbar.)

        # --- Splitter: TagPanel (left) + file table (right) ---
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter = self.splitter  # local alias, existing code below refers to it by this name
        root_layout.addWidget(splitter)

        self.tag_panel = TagPanel()
        self.tag_panel.apply_requested.connect(self._on_apply_to_selected)
        self.tag_panel.collapseToggleRequested.connect(self._toggle_tag_panel)
        self.tag_panel.play_button.clicked.connect(lambda _checked=False: self.actions_by_key["play"].trigger())
        # Deliberately low minimum -- lets the panel be dragged down to a
        # sliver, or fully collapsed (see _toggle_tag_panel()), rather
        # than being locked to a wide fixed range.
        self.tag_panel.setMinimumWidth(24)
        self.tag_panel.setMaximumWidth(440)
        splitter.addWidget(self.tag_panel)

        self.table = QTableWidget()
        # The header carries the per-column filters (funnel button on every filterable column); installed before
        # sorting is switched on and before any header signal is connected.
        self.filter_header = ColumnFilterHeader(self.table)
        self.table.setHorizontalHeader(self.filter_header)
        self.filter_header.configure(self._column_key, FILTER_COLUMN_KINDS)
        self.filter_header.filtersChanged.connect(self._on_filters_changed)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        # Click a header to sort by that column -- safe here because row
        # -> file mapping is FILE_ROLE (Qt.UserRole)-based, not list-index
        # based; _refresh_table_rows() suspends this while it repopulates,
        # see suspend_sorting()'s own docstring for why that's required,
        # not just tidy.
        self.table.setSortingEnabled(True)
        self.table.setStyleSheet(TABLE_SELECTION_STYLESHEET)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_table_context_menu)

        header = self.table.horizontalHeader()
        header.setSectionsMovable(True)  # click-and-drag column reordering, built into Qt
        header.sectionMoved.connect(self._on_column_moved)
        header.sectionResized.connect(self._on_column_resized)
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self._on_column_header_context_menu)

        splitter.addWidget(self.table)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setCollapsible(0, True)  # dragging the handle by hand can still reach 0 width
        splitter.setCollapsible(1, False)  # the table itself should never fully vanish
        splitter.splitterMoved.connect(self._on_splitter_moved)

        self._panel_collapser = SplitterPaneCollapser(
            self.splitter, pane_index=0, collapsed_width=TAG_PANEL_COLLAPSED_WIDTH, default_width=340,
        )

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

    def _look_up_entries(self, key_prefix: str, with_shortcuts: bool = True) -> list[MenuAction]:
        """The sources under Metadata > Look Up (the offline IMDb database and the online ones), and again in the
        row right-click menu (key_prefix keeps the keys unique; the context
        menu's copy carries no shortcuts, the menu bar's own actions hold
        them)."""
        def entry(key: str, text: str, slot, shortcut: str | None) -> MenuAction:
            return MenuAction(key_prefix + key, text, slot, shortcut=shortcut if with_shortcuts else None)

        return [
            entry("tmdb_movie", "TMDB (&Movie)…", lambda: self._on_import_tmdb("movie"), "Ctrl+M"),
            entry("tmdb_tv", "TMDB (&TV Show)…", lambda: self._on_import_tmdb("tv"), "Ctrl+T"),
            entry("tvdb", "TheTVDB (T&V Show)…", self._on_import_tvdb, "Ctrl+Shift+T"),
            entry("imdb_local", "IMDb (&Local Database)…", self._on_import_imdb_local, None),
            # Was Ctrl+Shift+O, which is Open Folder in the rest of the family.
            entry("subtitles", "&Subtitles (OpenSubtitles, Lyrics)…", self._on_import_subtitles, "Ctrl+Shift+L"),
        ]

    def _build_menu_bar(self) -> None:
        """Menu bar built through redactor_common's standard menu skeleton:
        File, Edit, View, Metadata, Media, Tools, Help, with the shared
        actions under their canonical labels (core/labels.py). The spec is in
        the family's menu-skeleton proposal (section C4 is this app).

        Every action is reused, never duplicated, on the toolbar built right
        after this in __init__ (see _build_toolbar) and in the row context
        menu: they come from the ActionRegistry the builder attaches to the
        window."""
        spec = StandardMenuSpec(
            file=_tidy(_drop_keys(standard_file_items(
                open_files=self._on_open_files,
                open_folder=self._on_open_folder,
                include_subfolders=include_subfolders_slot(),
                import_and_convert=self._on_import_and_convert,
                # One Save: it saves EVERY changed file (Ctrl+Shift+A).
                # A save-selected-only entry is gone: it was
                # almost never wanted, and two Saves next to each other only
                # invite the wrong one. The helper's separate Save entry would
                # be a dead duplicate, so it is dropped rather than shown greyed.
                save_all=self._on_save_all,
                rename_file=self.rename_selected_file,
                undo_last_rename=self.undo_last_rename,
                rename_export_move=self._on_rename_by_pattern,
                export_settings=self._on_export_settings,
                import_settings=self._on_import_settings,
                remove_from_list=self._on_remove_from_list,
                clear_list=self._on_clear_list,
                exit_slot=self.close,
            ), {"save"})),
            edit=standard_edit_items(
                undo=self.undo_last_action,
                redo=self.redo_last_action,
                apply=self._on_apply_menu,
                redact=self._on_redact,
                edit_redact_recipe=self._on_edit_redact_recipe,
                search_replace=self._on_search_replace,
                change_case=self._on_case_conversion,
                auto_number=self._on_auto_numbering,
            ),
            view=standard_view_items(
                show_metadata_panel=self._on_show_panel_toggled,
                # The zoom controller is built with the toolbar, after the menu.
                zoom_in=lambda: self.zoom.zoom_in(),
                zoom_out=lambda: self.zoom.zoom_out(),
                reset_zoom=lambda: self.zoom.zoom_reset(),
                # The family's F5 and Ctrl+R (Ctrl+R used to be Remux here; Remux is Ctrl+Shift+M now).
                refresh_list=self._refresh_list,
                extra_view=[MenuAction("clear_column_filters", "&Clear All Filters", self._clear_column_filters)],
            ),
            app_menus=[
                AppMenu(labels.MENU_METADATA, [
                    MenuAction("parse_filename", labels.PARSE_FILENAME,
                               self._on_import_metadata_from_filename, shortcut=shortcuts.PARSE_FILENAME),
                    Separator(),
                    look_up_submenu(self._look_up_entries("lookup_")),
                    Separator(),
                    MenuAction("number_episodes", "&Number Episodes…", self._on_number_episodes),
                ]),
                # Only the heavy video-specific operations: what acts on the
                # container/streams rather than on tags.
                AppMenu(labels.MENU_MEDIA, [
                    # Plays the file in its own player window (audio/subtitle track pickers). Ctrl+Shift+P: free.
                    MenuAction("play", "&Play", self._on_play_selected, shortcut="Ctrl+Shift+P"),
                    Separator(),
                    # Ctrl+Shift+M (M for MP4): Ctrl+R is Refresh List in the whole family.
                    MenuAction("remux", "&Remux to MP4…", self._on_remux_selected, shortcut="Ctrl+Shift+M"),
                    MenuAction("convert_to_mp4", "Con&vert to MP4 (H.264)…", self._on_convert_to_mp4,
                               shortcut="Ctrl+Shift+C"),
                    MenuAction("convert_rules", "Convert to MP4/MKV (R&ules)…", self._on_convert_with_rules),
                    Separator(),
                    MenuAction("check_files", "&Check Files…", self._on_check_files),
                    MenuAction("find_duplicates", labels.FIND_DUPLICATES, self._on_find_duplicates),
                    Separator(),
                    MenuAction("translate_subtitles", "&Translate Subtitles to English…", self._on_translate_subtitles),
                    MenuAction("compare_subtitles", "Co&mpare Subtitles…", self._on_compare_subtitles),
                ]),
            ],
            tools=_with_preferences(self._on_open_preferences, standard_tools_items(
                api_keys=self._on_add_external_apis,
                external_tools=self._on_locate_tools,
                app_settings=[MenuAction("imdb_settings", "IMDb &Database…", self._on_open_imdb_settings)],
                columns=self._on_open_column_visibility,
                genres=self._on_open_genres,
                languages=self._on_open_languages,
                history=self._on_open_history,
            )),
            help=standard_help_items(
                APP_NAME, self._on_show_changelog, self._on_show_credits, self._on_show_about,
            ),
        )
        build_standard_menu_bar(self, spec)
        registry = get_action_registry(self)
        self.actions_by_key = registry

        # Save All is Ctrl+Shift+A only: the one-release Ctrl+S / Ctrl+Shift+S
        # compatibility aliases were dropped (owner decision 2026-10-14).
        # F1 used to open About; F1 is Help contents everywhere else, so it is
        # simply unbound now (no alias: lint forbids F1 on About).

        # Apply used to be toolbar-only; it is a menu entry now (so the command
        # palette finds it) and keeps its toolbar button.
        registry["apply"].setToolTip(
            "Apply typed changes in the panel to the selected file(s) "
            "-- does not save to disk (Save already applies pending "
            "changes automatically, so this is only needed to stage "
            "changes without saving yet)"
        )
        registry["show_metadata_panel"].setChecked(True)

        # Back-compat: the rest of this file (toolbar) references these
        # as self.<x>_action attributes directly.
        self.open_files_action = registry["open_files"]
        self.open_folder_action = registry["open_folder"]
        attach_include_subfolders(
            registry["include_subfolders"], load=_load_include_subfolders, save=_save_include_subfolders,
        )
        self.save_all_action = registry["save_all"]
        self.rename_file_action = registry["rename_file"]
        self.remove_from_list_action = registry["remove_from_list"]
        self.apply_action = registry["apply"]
        set_apply_count(self.apply_action, 0)  # greyed until something is selected
        self.undo_action = registry["undo"]
        self.redact_action = registry["redact"]
        self.redo_action = registry["redo"]
        self.undo_action.setEnabled(False)
        self.redo_action.setEnabled(False)

        # Ctrl+K: searches every action above by name, menu path or key.
        add_command_palette(self, registry)

    def _build_toolbar(self) -> None:
        """Toolbar beneath the menu bar for the most-used actions, in the
        skeleton's order: Open Files, Open Folder | Apply | Save, Save All |
        Redact | Undo, Redo | zoom, Panel. Everything reuses the exact same
        QAction instances _build_menu_bar created (one source of truth per
        command: enabled state, text and shortcut stay in sync with the menu).
        Apply stays on the toolbar as the prominent path to it even though it
        is in the Edit menu now.

        setMovable(False) keeps it pinned directly under the menu bar
        rather than user-draggable to a window edge or floating --
        matches a fixed toolbar being the simpler, more predictable
        default for an app this size, and avoids "where did my toolbar
        go" confusion after an accidental drag.
        """
        toolbar = QToolBar("Main Toolbar", self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        toolbar.addAction(self.open_files_action)
        toolbar.addAction(self.open_folder_action)
        # A checkbox right after Open Folder, mirroring File > Include Subfolders (one saved value).
        self.include_subfolders_box = add_include_subfolders_checkbox(toolbar, self.actions_by_key["include_subfolders"])
        toolbar.addSeparator()
        # Plays the selected file (same action as Media > Play).
        toolbar.addAction(self.actions_by_key["play"])
        toolbar.addSeparator()

        toolbar.addAction(self.apply_action)
        toolbar.addSeparator()

        toolbar.addAction(self.save_all_action)
        toolbar.addSeparator()

        toolbar.addAction(self.redact_action)
        # The one-click action: bold so it stands out from the plain buttons.
        redact_button = toolbar.widgetForAction(self.redact_action)
        if redact_button is not None:
            redact_button.setStyleSheet("font-weight: bold; padding: 2px 10px;")
        toolbar.addSeparator()

        toolbar.addAction(self.undo_action)
        toolbar.addAction(self.redo_action)
        toolbar.addSeparator()

        # +/- table-font zoom, matching the epub tool's toolbar control
        # (this project never had one before) -- redactor_common's
        # TableZoomController owns the QAction pair + percentage label;
        # this window just places them. View > Zoom In/Out own Ctrl++ / Ctrl+-
        # now: the buttons were built from the same StandardKey bindings,
        # which would make the keys ambiguous, so the menu actions take over
        # every binding the buttons had and the buttons keep only their click.
        self.zoom = TableZoomController(self.table, parent=self)
        for menu_key, button in (("zoom_in", self.zoom.zoom_in_action), ("zoom_out", self.zoom.zoom_out_action)):
            with_aliases(self.actions_by_key[menu_key], *[k.toString() for k in button.shortcuts()])
            button.setShortcuts([])
        toolbar.addAction(self.zoom.zoom_out_action)
        toolbar.addWidget(self.zoom.label)
        toolbar.addAction(self.zoom.zoom_in_action)

        toolbar.addSeparator()

        # Minimize/restore the bulk-edit panel, matching the epub tool's
        # toolbar control (this project never had one before -- the
        # panel's own in-corner button and dragging the splitter handle
        # by hand were the only ways to do this). View > Show Metadata Panel
        # is the same toggle, kept in step by _sync_tag_panel_collapsed_indicator.
        toggle_panel_action = make_action(
            self, "Panel", self._toggle_tag_panel, tooltip="Minimize or restore the bulk-edit panel"
        )
        toolbar.addAction(toggle_panel_action)

    def _check_external_tools_on_startup(self) -> None:
        """Warn once at launch if ffmpeg and/or MKVToolNix aren't on
        PATH, with a direct download-page button per missing tool --
        matching the epub tool's v35 pattern of never letting a missing
        external tool surface as a raw, confusing subprocess error deep
        into some unrelated action.

        Deliberately non-blocking: the app still opens and is usable
        for whatever doesn't depend on the missing tool (e.g. MP4-only
        work still works fine without MKVToolNix) -- this warns, it
        doesn't refuse to run.
        """
        missing = missing_tools()
        if not missing:
            return

        from PyQt6.QtGui import QDesktopServices
        from PyQt6.QtCore import QUrl

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Missing Required Tools")
        lines = [f"\u2022 {t.name} -- needed for {t.used_for}" for t in missing]
        box.setText(
            "Some external tools this app depends on were not found on PATH:\n\n"
            + "\n".join(lines)
            + "\n\nYou can still use features that don't need them, but anything "
              "relying on a missing tool will fail until it's installed."
        )

        download_buttons = {}
        for tool in missing:
            btn = box.addButton(f"Open {tool.name} Download Page", QMessageBox.ButtonRole.ActionRole)
            download_buttons[btn] = tool.download_url
        locate_button = box.addButton("Locate Manually...", QMessageBox.ButtonRole.ActionRole)
        box.addButton("Continue", QMessageBox.ButtonRole.AcceptRole)

        box.exec()
        clicked = box.clickedButton()
        if clicked == locate_button:
            self._on_locate_tools()
        elif clicked in download_buttons:
            QDesktopServices.openUrl(QUrl(download_buttons[clicked]))
            # NOTE: exec() already returned by the time the URL opens, so
            # if more than one tool is missing, only the clicked one's
            # page opens -- the dialog doesn't reopen for the others.
            # Acceptable for v1 (the warning text still lists everything
            # missing either way) but worth revisiting if this ever
            # needs to handle "open every missing tool's page."

    # --- File > Export Settings / Import Settings --------------------------

    def _on_export_settings(self) -> None:
        export_settings(self, VideoSettingsAdapter(redetect_tools=self._redetect_tools))

    def _on_import_settings(self) -> None:
        import_settings(
            self, VideoSettingsAdapter(redetect_tools=self._redetect_tools),
            on_applied=self._after_settings_import,
        )

    def _after_settings_import(self, result) -> None:
        """Refreshes what reads its settings only when built: the table
        columns (order, visibility, widths) and the panel's fields (hidden
        columns, genre/language lists). Recipe, patterns and the defaults are
        read fresh each time their dialog opens, so they need nothing."""
        applied = set(result.applied)
        if applied & REFRESH_COLUMNS:
            self._rebuild_table_columns()
        if applied & REFRESH_PANEL:
            self.tag_panel.refresh_fields()
        self.status_bar.showMessage("Settings imported.")

    def _redetect_tools(self) -> None:
        """Offered after an import: tool lookup isn't cached, so re-detecting
        is just checking again (and prompting if something is missing)."""
        if missing_tools():
            self._check_external_tools_on_startup()
        else:
            self.status_bar.showMessage("All required tools are detected.")

    def _on_open_preferences(self) -> None:
        """Tools > Preferences: filenames, transcode, duplicates and tool
        paths. Everything is read fresh when the dialog that uses it opens,
        so nothing needs refreshing afterwards; the tool paths are re-checked
        because that page saves as you go."""
        VideoPreferencesDialog(self).exec()
        if missing_tools():
            self.status_bar.showMessage("Some required tools are still missing.")

    def _on_locate_tools(self) -> None:
        """Open the tool-location settings dialog (Tools menu, or
        the 'Locate Manually...' button on the startup missing-tools
        warning). After it closes, re-check and give explicit feedback
        -- confirms the fix actually worked rather than leaving the
        user to guess whether their configured path was correct.
        """
        dialog = ToolSettingsDialog(parent=self)
        dialog.exec()

        still_missing = missing_tools()
        if not still_missing:
            self.status_bar.showMessage("All required tools are now detected.")
        else:
            names = ", ".join(t.name for t in still_missing)
            self.status_bar.showMessage(f"Still missing: {names}")

    def _on_add_external_apis(self) -> None:
        """Add/Edit External APIs (Tools menu) -- lets the user
        enter TMDB/TheTVDB API keys directly rather than needing an
        environment variable or hand-edited settings.ini. Nothing needs
        refreshing after it closes; the key is only read at the moment
        an import is actually attempted.
        """
        dialog = ApiKeysDialog(parent=self)
        dialog.exec()

    def _on_open_imdb_settings(self) -> None:
        """Tools > IMDb Database...: where the offline IMDb database is, and
        building it from the user's own IMDb dataset files."""
        from gui.imdb_settings_dialog import ImdbSettingsDialog

        ImdbSettingsDialog(self).exec()

    def _on_open_column_visibility(self) -> None:
        """Add/Remove Columns (Tools menu) -- redactor_common's shared
        ColumnSettingsDialog (the same one cbz/mp3 use), reading and
        writing the same persisted hidden-columns state as the table
        header's right-click menu. Refreshes both the table and the
        bulk-edit panel if anything changed ("hiding a column also hides
        its panel field")."""
        before = sanitize_hidden_fields(self._load_hidden_fields())
        dialog = ColumnSettingsDialog(
            list(COLUMN_LABEL_LOOKUP.items()), before,
            protected_columns=PROTECTED_COLUMNS, parent=self,
        )
        dialog.exec()
        hidden = sanitize_hidden_fields(dialog.hidden_fields())
        if hidden != before:
            set_setting("table", "hidden_columns", ",".join(sorted(hidden)))
            self._on_column_visibility_changed_via_settings()

    def _on_open_history(self) -> None:
        """Tools > History...: the operations journal (every tag change this app made, with the old values)
        in redactor_common's HistoryDialog. A restore runs on a worker thread (the dialog's run_busy), so the
        wrapper only records which files it wrote; once the dialog is closed, those that are loaded here are
        re-read from disk on the GUI thread and their rows refreshed."""
        # Opt-in (off by default): with nothing recorded yet, offer to turn it on first.
        enabled = history.history_enabled()
        if not enabled and not history.journal_exists():
            answer = QMessageBox.question(
                self, "History",
                "History is off. Turn it on? From then on, tag changes this program saves are recorded here. "
                "Nothing has been recorded so far.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            history.set_history_enabled(True)
            enabled = True
        restored: list[str] = []

        def apply_restore(path: str, values: dict[str, str]) -> str:
            problem = history.apply_restore(path, values)
            if not problem:
                restored.append(path)
            return problem

        title = "History" if enabled else "History (recording is off)"
        HistoryDialog(history.get_journal(), apply_restore, self, title).exec()
        if restored:
            self._reload_restored(restored)

    def _reload_restored(self, paths: list[str]) -> None:
        """Re-reads the loaded files among `paths` (written by a History restore) under a progress dialog and
        refreshes the rows. An unsaved edit on such a file is dropped: the file on disk changed under it."""
        wanted = {os.path.normcase(os.path.abspath(p)) for p in paths}
        stale = [vf for vf in self.video_files if os.path.normcase(os.path.abspath(str(vf.path))) in wanted]
        if not stale:
            return

        def step(vf: VideoFile, _index: int) -> None:
            vf.load()
            vf.dirty = False
            vf.save_error = ""
            vf._thumbnail_path = None

        run_with_progress(
            self, stale, step, "Re-reading restored files...", threshold=1, cancellable=False,
            label_for=lambda vf: f"Reading {vf.path.name}",
        )
        self._clear_undo()  # its snapshots predate the restore, like Refresh List
        self._after_batch_edit(f"Re-read {len(stale)} restored file(s)")

    def _on_open_languages(self) -> None:
        """Add/Remove Languages (Tools menu) -- refreshes the bulk-
        edit panel's language picker immediately if anything changed,
        rather than only on the next filter change or file selection.
        """
        dialog = VocabularyEditorDialog(
            "Languages", get_language_options, add_language_option, remove_language_option, parent=self,
        )
        dialog.exec()
        if dialog.changed:
            self.tag_panel.refresh_fields()

    def _on_open_genres(self) -> None:
        """Add/Remove Genres (Tools menu) -- same immediate-refresh
        reasoning as _on_open_languages.
        """
        dialog = VocabularyEditorDialog(
            "Genres", get_genre_options, add_genre_option, remove_genre_option, parent=self,
        )
        dialog.exec()
        if dialog.changed:
            self.tag_panel.refresh_fields()

    def _on_column_visibility_changed_via_settings(self) -> None:
        """Rebuild both the table and the bulk-edit panel after a
        column visibility change made via Settings' Add/Remove
        Columns dialog -- kept as one named method rather than an
        inline lambda specifically because it now does two things, not
        one, and a lambda doing two dispatches reads worse than a
        method with a name that says so.
        """
        self._rebuild_table_columns()
        self.tag_panel.refresh_fields()

    # --- Column / filter handling -------------------------------------

    def _column_key(self, column: int) -> str:
        """Logical column index -> field name (the key the header's filters are stored under)."""
        return self._column_order[column]

    def _rebuild_table_columns(self) -> None:
        """Rebuild the table columns: every field, in the user's persisted drag-order, with the persisted
        hidden-column and width preferences applied. (The Content Type filter no longer changes which columns
        exist: it is a row filter in the column header now, see ColumnFilterHeader.)"""
        field_names = list(FIELD_LABELS.keys())

        rest = [f for f in field_names if f != "content_type"]
        base_order = ALWAYS_VISIBLE_COLUMNS + rest + TECHNICAL_COLUMNS

        persisted_order = self._load_persisted_column_order()
        self._column_order = merge_column_order(persisted_order, base_order)

        self._suppress_column_signals = True
        try:
            self.table.setColumnCount(len(self._column_order))
            label_lookup = COLUMN_LABEL_LOOKUP
            headers = [label_lookup.get(f, f) for f in self._column_order]
            self.table.setHorizontalHeaderLabels(headers)

            hidden = sanitize_hidden_fields(self._load_hidden_fields())
            widths = self._load_column_widths()
            for idx, field_name in enumerate(self._column_order):
                self.table.setColumnHidden(idx, not is_column_visible(field_name, hidden))
                if field_name in widths:
                    self.table.setColumnWidth(idx, widths[field_name])
        finally:
            self._suppress_column_signals = False

        self._refresh_table_rows()
        self._apply_resizable_columns()

    def _apply_resizable_columns(self) -> None:
        """Every column draggable (house rule) with sensible starting widths from the filled table; the user's
        persisted widths still win over those starting widths, and nothing here is saved as a user choice."""
        self._suppress_column_signals = True
        try:
            make_columns_resizable(self.table, wide_column=None)
            widths = self._load_column_widths()
            for idx, field_name in enumerate(self._column_order):
                if field_name in widths and not self.table.isColumnHidden(idx):
                    self.table.setColumnWidth(idx, widths[field_name])
        finally:
            self._suppress_column_signals = False

    # --- Column order / visibility / width persistence -------------------
    # Stored in settings.ini under [table] via core/config.py. Business
    # logic (merging, visibility rules) lives in core/table_settings.py
    # and is unit-tested there; these methods are thin Qt-facing wiring.

    def _load_persisted_column_order(self) -> list[str]:
        raw = get_setting("table", "column_order", "")
        return [f for f in raw.split(",") if f] if raw else []

    def _load_hidden_fields(self) -> set[str]:
        raw = get_setting("table", "hidden_columns", "")
        return {f for f in raw.split(",") if f} if raw else set()

    def _load_column_widths(self) -> dict[str, int]:
        raw = get_setting("table", "column_widths", "")
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            # A hand-edited or corrupted settings.ini shouldn't crash the
            # app -- just fall back to "no persisted widths" and move on.
            return {}

    def _current_visual_column_order(self) -> list[str]:
        """Field names in their current ON-SCREEN (visual) order, not
        creation/logical order -- reads Qt's header.logicalIndex(visual
        position) mapping, since dragging a column changes its visual
        position while its logical index (and therefore its meaning to
        the rest of the code, e.g. column 0 always being the filename
        cell for VideoFile lookups) never changes.
        """
        header = self.table.horizontalHeader()
        order = []
        for visual_pos in range(header.count()):
            logical_idx = header.logicalIndex(visual_pos)
            if 0 <= logical_idx < len(self._column_order):
                order.append(self._column_order[logical_idx])
        return order

    def _on_column_moved(self, logical_index: int, old_visual_index: int, new_visual_index: int) -> None:
        if self._suppress_column_signals:
            return
        order = self._current_visual_column_order()
        set_setting("table", "column_order", ",".join(order))

    def _on_column_resized(self, logical_index: int, old_size: int, new_size: int) -> None:
        if self._suppress_column_signals:
            return
        if logical_index >= len(self._column_order):
            return
        if self.table.isColumnHidden(logical_index):
            # Hiding a column can itself fire this signal with a
            # near-zero size -- never let that overwrite a real
            # persisted width, or un-hiding the column later would
            # restore it at width 0.
            return
        field_name = self._column_order[logical_index]
        widths = self._load_column_widths()
        widths[field_name] = new_size
        set_setting("table", "column_widths", json.dumps(widths))

    def _on_column_header_context_menu(self, pos) -> None:
        """Right-click on a column header -> checklist of every current
        candidate column, letting the user show/hide any of them, plus
        (new) a link to the same Add/Remove Columns dialog the Settings
        menu already offers. `filename` is never offered here -- it's
        the row-identity anchor every lookup depends on, so it's not
        something the app should ever let get hidden by accident (or on
        purpose).
        """
        show_column_header_context_menu(
            self, self.table, pos,
            column_order=self._column_order,
            label_lookup=COLUMN_LABEL_LOOKUP,
            protected_columns=frozenset({"filename"}),
            hidden_fields=sanitize_hidden_fields(self._load_hidden_fields()),
            is_visible=is_column_visible,
            on_toggle=self._on_column_visibility_toggled,
            open_column_settings_dialog=self._on_open_column_visibility,
            top_actions=[("Clear All Filters", self._clear_column_filters, self.filter_header.filters.any_active())],
        )

    def _on_column_visibility_toggled(self, field_name: str, checked: bool) -> None:
        hidden = self._load_hidden_fields()
        if checked:
            hidden.discard(field_name)
        else:
            hidden.add(field_name)
        hidden = sanitize_hidden_fields(hidden)
        set_setting("table", "hidden_columns", ",".join(sorted(hidden)))

        # Apply immediately to the live table rather than waiting for
        # the next filter change to trigger a full rebuild.
        for idx, f in enumerate(self._column_order):
            if f == field_name:
                self.table.setColumnHidden(idx, not checked)
                break

        # Same hidden-columns state now also drives the bulk-edit
        # panel's field list (per explicit request: hiding a column
        # should hide its panel field too) -- refresh it here too, not
        # just via the Settings dialog's own Add/Remove Columns entry
        # point, so both places that can hide a column keep the panel
        # in sync the same way.
        self.tag_panel.refresh_fields()

    def _on_filters_changed(self) -> None:
        """A column filter was set or cleared in a header funnel: hide/show the rows and say what is shown."""
        self._apply_column_filters(announce=True)

    def _apply_column_filters(self, announce: bool = False) -> None:
        """Hides the rows the header filters reject (deselecting any selected row that gets hidden, so a batch
        action only reaches visible files). Re-run after every table rebuild: a rebuild shows all rows again.
        Hidden rows stay in self.video_files; see _visible_video_files for which actions mean 'all visible'
        and which mean 'all loaded'."""
        header = self.filter_header
        shown, total = header.apply_to_table()
        if announce:
            if header.filters.any_active():
                names = header.filters.describe(lambda key: COLUMN_LABEL_LOOKUP.get(key, str(key)))
                self.status_bar.showMessage(f"Showing {shown} of {total} files (filtered by {names})")
            else:
                self.status_bar.showMessage(f"Filters cleared: showing all {total} files")
        # (apply_to_table already signals a selection change when it deselected a hidden row.)

    def _clear_column_filters(self) -> None:
        self.filter_header.clear_all_filters()

    def _visible_video_files(self) -> list[VideoFile]:
        """Every loaded file whose row is not hidden by a column filter, in list order. With no filter active
        that is every loaded file. Used where an action means 'all the files I can see' when nothing is
        selected (Redact, batch edits, Check Files); Save All, Find Duplicates and Clear List deliberately
        keep working on every loaded file."""
        if not self.filter_header.filters.any_active():
            return list(self.video_files)
        visible = {id(vf) for row in range(self.table.rowCount())
                   if not self.table.isRowHidden(row) and (vf := self._file_for_row(row)) is not None}
        return [vf for vf in self.video_files if id(vf) in visible]

    # --- Loading files ---------------------------------------------------

    def _on_open_files(self) -> None:
        """File > Open Files...: picks individual videos and ADDS them to the
        list (Open Folder replaces it), skipping any already loaded."""
        start = get_setting("general", "last_files_folder", "") or get_setting("general", "last_folder", "")
        patterns = " ".join(f"*{ext}" for ext in sorted(SUPPORTED_EXTENSIONS))
        chosen, _ = QFileDialog.getOpenFileNames(self, "Open Video Files", start, f"Video Files ({patterns})")
        if not chosen:
            return
        set_setting("general", "last_files_folder", str(Path(chosen[0]).parent))
        self._add_files([Path(p) for p in chosen])

    def _add_files(self, paths: list[Path]) -> None:
        """Loads `paths` and appends them to the list; a path already in it
        is skipped (same file twice would be two rows editing one file)."""
        def key(p) -> str:
            return os.path.normcase(os.path.abspath(str(p)))

        known = {key(vf.path) for vf in self.video_files}
        fresh: list[Path] = []
        for p in paths:
            if key(p) not in known:
                known.add(key(p))
                fresh.append(p)
        skipped = len(paths) - len(fresh)

        def _step(p: Path, _index: int) -> None:
            vf = VideoFile(path=p)
            vf.load()
            self.video_files.append(vf)

        before = len(self.video_files)
        cancelled = not run_with_progress(
            self, fresh, _step, "Loading video files...", threshold=3,
            label_for=lambda p: f"Loading {p.name}",
        )
        self._refresh_table_rows()
        added = self.video_files[before:]
        failed = sum(1 for vf in added if vf.load_error)
        msg = f"Added {len(added) - failed} file(s)"
        if cancelled:
            msg = f"Cancelled -- {msg.lower()}"
        if skipped:
            msg += f", {skipped} already in the list"
        if failed:
            msg += f", {failed} failed to read"
        self.status_bar.showMessage(msg)

    def _on_remove_from_list(self) -> None:
        """File > Remove from List (Delete): drops the selected files from
        the list. Nothing on disk is touched; unsaved edits on them are lost,
        so that is confirmed first. The Undo stack is cleared (it may point at
        the removed files), like any other replacement of the list."""
        selected = self._selected_video_files()
        if not selected:
            self.status_bar.showMessage("No files selected")
            return
        dirty = sum(1 for vf in selected if vf.dirty)
        if dirty and not self._confirm_discard(
            f"remove {len(selected)} file(s) from the list (discarding unsaved changes on {dirty})"
        ):
            return
        gone = {id(vf) for vf in selected}
        self.video_files = [vf for vf in self.video_files if id(vf) not in gone]
        self._clear_undo()
        self._refresh_table_rows()
        self.status_bar.showMessage(f"Removed {len(selected)} file(s) from the list (files on disk untouched)")

    def _on_clear_list(self) -> None:
        """File > Clear List: empties the list (nothing on disk is touched),
        confirming first when any file has unsaved edits."""
        if not self.video_files:
            self.status_bar.showMessage("The list is already empty")
            return
        if self._count_dirty() and not self._confirm_discard("clear the list (discarding unsaved changes)"):
            return
        count = len(self.video_files)
        self.video_files = []
        self._clear_undo()
        self._refresh_table_rows()
        self.status_bar.showMessage(f"Cleared the list ({count} file(s) removed; files on disk untouched)")

    def _on_open_folder(self) -> None:
        # Start the browser at the last folder actually opened, rather
        # than always defaulting to some OS-chosen starting point --
        # saves re-navigating to the same media folder every session.
        last_folder = get_setting("general", "last_folder", "")
        folder = QFileDialog.getExistingDirectory(self, "Select Folder", last_folder)
        if not folder:
            return
        folder_path = Path(folder)
        set_setting("general", "last_folder", str(folder_path))

        # File > Include Subfolders (redactor_common's shared item) decides; no question is asked any more.
        self._load_folder(folder_path, recursive=_load_include_subfolders())

    def _load_folder(self, folder_path: Path, recursive: bool, status_suffix: str = "") -> None:
        """Shared loading routine -- discovers files, shows the
        progress dialog for heavy folders, and populates the table.
        Used by both _on_open_folder (manual, via the picker) and
        _restore_last_folder_on_startup (automatic, on launch) so the
        two paths can't silently drift out of sync with each other.

        status_suffix is appended to the final status bar message
        (e.g. " (restored from last session)") without needing the
        caller to duplicate the whole message-assembly logic below.
        """
        ignored: list[Path] = []
        paths = run_busy(self, "Looking for video files...", discover_video_files, folder_path, recursive=recursive, ignored=ignored)

        # Replaces self.video_files wholesale -- shared by both Open Folder
        # and the startup restore below, so this one check covers both call
        # sites. Startup restore always finds video_files empty (nothing's
        # been loaded yet), so _count_dirty() is always 0 there and this
        # never actually prompts on launch.
        if self._count_dirty() and not self._confirm_discard("load a new folder (discarding unsaved changes)"):
            return
        self.video_files = []
        self._clear_undo()

        def _step(p: Path, _index: int) -> None:
            vf = VideoFile(path=p)
            vf.load()
            self.video_files.append(vf)

        # The shared helper: threshold-gated, fixed width (the old
        # hand-rolled dialog grew with each long "Loading <name>" label
        # and never shrank back), per-file label elided, Cancel works.
        cancelled = not run_with_progress(
            self, paths, _step, "Loading video files...", threshold=3,
            label_for=lambda p: f"Loading {p.name}",
        )

        self._refresh_table_rows()
        loaded = sum(1 for vf in self.video_files if not vf.load_error)
        failed = len(self.video_files) - loaded
        if cancelled:
            msg = f"Cancelled -- loaded {loaded} of {len(paths)} file(s)"
        else:
            msg = f"Loaded {loaded} file(s)"
        if recursive:
            msg += " (including subfolders)"
        if failed:
            msg += f", {failed} failed to read"
        if ignored:
            # Leftovers of an interrupted repair/convert/Redact: skipped
            # quietly, just noted here (no dialog).
            msg += f" -- Ignored {len(ignored)} leftover temp file(s)"
        msg += status_suffix
        self.status_bar.showMessage(msg)
        if not cancelled and not status_suffix:  # not on the automatic restore at launch
            self._offer_import_of_convertible(folder_path, recursive, loaded)

    def _offer_import_of_convertible(self, folder_path: Path, recursive: bool, loaded: int) -> None:
        """Open Folder loads only MP4/MKV. Say what else Import & Convert could bring in from the folder (and its
        subfolders, if included), and offer to convert it."""
        others = run_busy(self, "Looking for other video formats...", discover_convertible_files, folder_path, recursive=recursive)
        if not others:
            return
        # One dialog (redactor_common's convert offer): the per-run "Dry run first" choice is asked here, so no
        # second confirm follows.
        dry_box = self._dry_run_checkbox()
        answer = offer_conversion(
            self, others, loaded, kind_label="MP4 or MKV", target_label="MP4/MKV", noun="video file",
            question="Convert them now with the conversion rules?\n\n" + self._rules_text(),
            title="Other Video Formats Found", yes_text="Yes", no_text="No", options=[dry_box],
        )
        if answer == CONVERT_YES:
            self._convert_with_rules([str(p) for p in others], dry_run=dry_box.isChecked())

    def _refresh_list(self) -> None:
        """Re-scans the folder(s) your currently-loaded files live in
        (picking up any new video file dropped there since you
        loaded), then re-reads everything still present fresh from
        disk. Doesn't discover a brand-new folder nothing's been
        loaded from at all -- only folders already represented in the
        current list get scanned, non-recursively -- use Open Folder
        for an actual new folder.

        Discards unsaved in-memory edits (with confirmation first) --
        same discard-confirmation mechanism _load_folder now has for
        Open Folder, applied here too since this replaces the list
        just as wholesale.

        The "what's new on disk" logic itself is
        redactor_common.core.folder_refresh's -- this is just the
        video-specific wiring: how paths come out of self.video_files,
        and what to do once the new set is known."""
        if not self.video_files:
            self.status_bar.showMessage("Nothing to refresh: the list is empty (use Open Folder first)")
            return
        if self._count_dirty() and not self._confirm_discard("refresh the list (discarding unsaved changes)"):
            return

        existing_paths = [str(vf.path) for vf in self.video_files]
        recursive = _load_include_subfolders()
        new_paths = run_busy(
            self, "Looking for new files...", find_new_files_in_loaded_folders,
            existing_paths,
            lambda folder: [str(p) for p in discover_video_files(Path(folder), recursive=recursive)],
        )
        # Files that are gone from disk are dropped; only present ones are shown.
        present_paths = [p for p in existing_paths if os.path.exists(p)]
        removed_count = len(existing_paths) - len(present_paths)
        all_paths = [Path(p) for p in present_paths + new_paths]

        self.video_files = []
        self._clear_undo()

        def _step(p: Path, _index: int) -> None:
            vf = VideoFile(path=p)
            vf.load()
            self.video_files.append(vf)

        # Not cancellable: the old list is already gone, so stopping
        # halfway would silently drop the rest of the loaded files.
        run_with_progress(
            self, all_paths, _step, "Refreshing...", threshold=3, cancellable=False,
            label_for=lambda p: f"Loading {p.name}",
        )

        self._refresh_table_rows()

        loaded = sum(1 for vf in self.video_files if not vf.load_error)
        failed = len(self.video_files) - loaded
        if new_paths:
            msg = f"Found {len(new_paths)} new file(s), reloaded {loaded} file(s)"
        else:
            msg = f"No new files found, reloaded {loaded} file(s)"
        if failed:
            msg += f", {failed} failed to read"
        if removed_count:
            msg += f", removed {removed_count} no longer on disk"
        self.status_bar.showMessage(msg)

    def _restore_last_folder_on_startup(self) -> None:
        """Auto-load the last-opened folder's files on launch -- the
        actual "remember" behavior a user means by that word (close
        the app with a folder open, relaunch, see it again), distinct
        from _on_open_folder's picker-dialog-starting-location memory,
        which only helps the NEXT TIME the user manually clicks Open
        Folder rather than restoring anything automatically. Both are
        real, complementary things "remember the last folder" can mean;
        this covers the stronger one directly.

        Silent no-op if there's no persisted folder yet, or if the
        persisted path no longer exists (moved/deleted/unmounted drive
        since last session) -- a missing folder on startup isn't worth
        an error dialog interrupting the very first thing the user
        sees when opening the app.

        Follows File > Include Subfolders like a manual Open Folder (no question is asked on launch).
        """
        last_folder = get_setting("general", "last_folder", "")
        if not last_folder:
            return
        folder_path = Path(last_folder)
        if not folder_path.is_dir():
            return
        self._load_folder(folder_path, recursive=_load_include_subfolders(), status_suffix=" (restored from last session)")

    # --- Table rendering ---------------------------------------------------

    def _refresh_table_rows(self) -> None:
        # Rows are refilled in self.video_files order and then re-sorted,
        # so a selection kept by row number would land on whichever file
        # now sits in that row. Found 2026-09-29: in a table sorted by a
        # column, Apply on S01E01 left S01E03 selected, and the NEXT
        # Apply silently edited S01E03. Reselect by file instead.
        selected_ids = [id(vf) for vf in self._selected_video_files()]
        with suspend_sorting(self.table):
            self.table.setRowCount(len(self.video_files))
            for row, vf in enumerate(self.video_files):
                row_colors = self._row_colors(vf)
                for col, field_name in enumerate(self._column_order):
                    text = self._display_value(vf, field_name)
                    item = self._make_table_item(vf, field_name, text)
                    if col == 0:
                        # Store the VideoFile reference on the filename cell --
                        # row->file mapping via UserRole, not row index.
                        item.setData(FILE_ROLE, vf)
                    check_note = vf.check.summary() if vf.check is not None and vf.check.findings else ""
                    stamp = vf.stamp
                    stamp_note = stamp.tooltip() if stamp is not None and field_name == "status" else ""
                    tooltip = "\n".join(t for t in (vf.save_error or vf.load_error, check_note, stamp_note) if t)
                    if tooltip:
                        item.setToolTip(tooltip)
                    if row_colors is not None:
                        bg, fg = row_colors
                        item.setBackground(bg)
                        item.setForeground(fg)
                    self.table.setItem(row, col, item)
        self._reselect_files(selected_ids)
        self._apply_column_filters()  # the rebuild showed every row again

    def _reselect_files(self, file_ids: list[int]) -> None:
        """Selects exactly the rows holding these VideoFiles (by id()),
        signals blocked so a pending panel edit isn't wiped. Only if a
        file dropped out of the selection (e.g. it was removed) does the
        panel get reloaded."""
        wanted = set(file_ids)
        selection = QItemSelection()
        first_row = -1
        found: list[int] = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            vf = item.data(FILE_ROLE) if item is not None else None
            if vf is not None and id(vf) in wanted:
                selection.select(self.table.model().index(row, 0),
                                 self.table.model().index(row, self.table.columnCount() - 1))
                found.append(id(vf))
                if first_row < 0:
                    first_row = row
        model = self.table.selectionModel()
        was_blocked = self.table.blockSignals(True)
        try:
            model.clearSelection()
            if first_row >= 0:
                model.setCurrentIndex(self.table.model().index(first_row, 0),
                                      QItemSelectionModel.SelectionFlag.NoUpdate)
                model.select(selection, QItemSelectionModel.SelectionFlag.Select)
        finally:
            self.table.blockSignals(was_blocked)
        if len(found) != len(wanted):
            self._on_selection_changed()

    def _make_table_item(self, vf: VideoFile, field_name: str, text: str) -> QTableWidgetItem:
        """Numeric-looking columns sort as numbers, not text ("2" before
        "10", not "10" before "2"). Duration/Size display a formatted
        string ("1:30:25", "1.2 MB") that isn't itself a bare number, so
        their real underlying value is passed explicitly as sort_value
        rather than relying on NumericTableWidgetItem parsing it back
        out of the formatted text (which would just fail and silently
        fall back to text order)."""
        if field_name == "duration_seconds":
            return NumericTableWidgetItem(text, sort_value=vf.metadata.duration_seconds)
        if field_name == "size_bytes":
            size = vf.size_bytes
            return NumericTableWidgetItem(text, sort_value=float(size) if size is not None else None)
        if field_name == "bitrate":
            return NumericTableWidgetItem(text, sort_value=bitrate_bps(vf.metadata.bitrate))
        if field_name in NUMERIC_FIELDS:
            return NumericTableWidgetItem(text)
        return QTableWidgetItem(text)

    def _row_colors(self, vf: VideoFile) -> Optional[tuple[QColor, QColor]]:
        """Returns a (background, foreground) pair, or None for a
        normal/unhighlighted row (which keeps the theme's own default
        colors for both, exactly as before -- only highlighted rows get
        an explicit, theme-independent color pair). Error takes
        priority over dirty -- a file that failed to save is more
        urgent to notice than one with merely-unsaved edits.
        """
        if vf.load_error or vf.save_error:
            return (ERROR_COLOR, HIGHLIGHT_TEXT_COLOR)
        if vf.dirty:
            return (DIRTY_COLOR, HIGHLIGHT_TEXT_COLOR)
        # Check Files results (this session's, or a current stamp read
        # from the file): damaged red like a load error, repairable in
        # the shared soft orange (needs attention, not broken).
        scan_status = vf.scan_status()
        if scan_status == "DAMAGED":
            return (ERROR_COLOR, HIGHLIGHT_TEXT_COLOR)
        if scan_status == "REPAIRABLE":
            return (SAVE_FAILED_COLOR, HIGHLIGHT_TEXT_COLOR)
        return None

    def _display_value(self, vf: VideoFile, field_name: str) -> str:
        if field_name == "filename":
            return vf.path.name
        if field_name == "path":
            return str(vf.path)
        if field_name == "status":
            if vf.load_error:
                # A file whose tags can't be read (e.g. an MKV without
                # MKVToolNix installed) can still have been checked: a
                # damaged/repairable result is what to act on, so it wins
                # (the load error stays in the tooltip); otherwise a
                # damaged file would look like any other load error.
                if vf.check is not None and vf.check.status in ("DAMAGED", "REPAIRABLE"):
                    return vf.check.status
                return "LOAD ERROR"
            if vf.save_error:
                return "SAVE FAILED"
            if vf.dirty:
                # A fresh scan marks the file dirty (its stamp is unsaved);
                # keep its result visible.
                return f"UNSAVED · {vf.check.status}" if vf.check is not None else "UNSAVED"
            # Media > Check Files...: the stamp (status + when) in
            # place of the unscanned text; a stale one says so.
            return vf.stamp_text() or (vf.check.status if vf.check is not None else "OK")
        if field_name == "content_type":
            ct = vf.metadata.content_type
            return ct.value if isinstance(ct, ContentType) else str(ct or "")
        if field_name == "duration_seconds":
            return format_duration(vf.metadata.duration_seconds)
        if field_name == "size_bytes":
            # Not a VideoMetadata field -- a live filesystem stat via
            # VideoFile's own property, so this can't go through the
            # generic getattr(vf.metadata, ...) fallback below.
            return format_file_size(vf.size_bytes)
        if field_name == "bitrate":
            return format_bitrate(vf.metadata.bitrate)
        value = getattr(vf.metadata, field_name, "")
        return "" if value is None else str(value)

    # --- Selection -> TagPanel -------------------------------------------

    def _file_for_row(self, row: int) -> Optional[VideoFile]:
        """Table row -> VideoFile via FILE_ROLE. The table is sortable, so a
        visual row is NOT an index into self.video_files."""
        item = self.table.item(row, 0)
        return item.data(FILE_ROLE) if item else None

    def _selected_video_files(self) -> list[VideoFile]:
        files: list[VideoFile] = []
        seen_rows = set()
        for item in self.table.selectedItems():
            if item.row() in seen_rows or self.table.isRowHidden(item.row()):
                continue  # a row hidden by a column filter is never a target, even if Select All reached it
            seen_rows.add(item.row())
            filename_item = self.table.item(item.row(), 0)
            vf = filename_item.data(FILE_ROLE) if filename_item else None
            if vf is not None:
                files.append(vf)
        return files

    def _show_table_context_menu(self, pos) -> None:
        """New: this project never had a row right-click menu before --
        the shared helper gets it the selection-fix (right-click outside
        the current selection replaces it, matching Explorer) and the
        two generic file actions (Open Containing Folder, Copy Path) for
        free, same as epub/mp3.
        """
        def extra_items(files: list[VideoFile]) -> list:
            items: list = [Separator()]
            # Only offered for a single file -- renaming several files
            # to the same name doesn't make sense. Also reachable by
            # double-clicking the Filename cell (see _on_cell_double_clicked()).
            # Reuses the actual File-menu QAction (F2) rather than
            # building a fresh one -- same object, so this shows the
            # real shortcut hint and can never drift out of sync with it.
            if len(files) == 1 and not files[0].load_error:
                items.extend([self.rename_file_action, Separator()])
            # Every lookup from the Metadata menu, so none needs a trip to
            # the menu bar (no shortcuts here: they already live on the
            # menu's own actions).
            if len(files) == 1 and not files[0].load_error:
                items.extend([self.actions_by_key["play"], Separator()])
            items.append(Submenu("Look Up", self._look_up_entries("ctx_", with_shortcuts=False)))
            items.append(Submenu("Organize", [
                self.actions_by_key["rename_export_move"],
                MenuAction("ctx_number_episodes", "Number Episodes…", lambda: self._quick_number_episodes(files)),
            ]))
            items.append(Submenu("Subtitles", [
                self.actions_by_key["translate_subtitles"],
                self.actions_by_key["compare_subtitles"],
            ]))
            items.extend([Separator(), self.redact_action, Separator(), self.remove_from_list_action])
            return items

        show_table_context_menu(
            self, self.table, pos,
            get_selected_items=self._selected_video_files,
            get_path=lambda vf: vf.path,
            extra_items=extra_items,
        )

    def _on_play_selected(self) -> None:
        """Media > Play (and the toolbar Play button): opens the shared player window for the one selected
        file and starts playing at once. Read-only: the file is never written."""
        files = self._selected_video_files()
        if len(files) != 1:
            self.status_bar.showMessage("Play needs exactly one file selected")
            return
        if files[0].load_error:
            self.status_bar.showMessage(f"Can't play {files[0].path.name}: it failed to load")
            return
        from redactor_common.gui.media_preview import show_media_preview
        show_media_preview(self, files[0].path, video=True, autoplay=True)

    def _on_cell_double_clicked(self, row: int, col: int) -> None:
        if not (0 <= col < len(self._column_order)) or self._column_order[col] != "filename":
            return
        vf = self._file_for_row(row)
        if vf is not None and vf.load_error:
            self.status_bar.showMessage(f"Can't rename {vf.path.name}: it failed to load")
        elif vf is not None:
            self.rename_single_file(vf)

    def rename_single_file(self, vf: VideoFile) -> None:
        """Quick, direct rename of a single file on disk -- for fixing a
        typo or small mistake in the filename directly. A physical file
        operation -- not tracked by any undo mechanism, same as
        Save/other on-disk operations. Triggered by double-clicking a
        Filename cell, or via the table's right-click menu.

        The prompt/validate/rename/error-report flow itself lives in
        redactor_common.gui.rename_single_file (imported above as
        prompt_rename_single_file to avoid shadowing this method's own
        name)."""
        if prompt_rename_single_file(self, str(vf.path), lambda p: setattr(vf, "path", Path(p)), log=_rename_log()):
            self._refresh_table_rows()

    def undo_last_rename(self) -> None:
        """File > Undo Last Rename...: renames the newest logged rename back
        (redactor_common's rename log -- renames aren't on the Undo stack,
        which covers metadata edits only)."""
        def restored(new_path: str, old_path: str) -> None:
            wanted = os.path.normcase(os.path.abspath(new_path))
            for item in self.video_files:
                if os.path.normcase(os.path.abspath(str(item.path))) == wanted:
                    item.path = Path(old_path)

        if undo_last_rename(self, _rename_log(), restored):
            self._refresh_table_rows()

    def rename_selected_file(self) -> None:
        """F2 entry point (Explorer convention: select one item, press
        F2, rename it directly) -- same guard the right-click "Rename
        File..." item uses (exactly one file selected, no load error),
        since F2 and that menu item are the same action reached two
        ways."""
        files = self._selected_video_files()
        if not files:
            self.status_bar.showMessage("Rename File: select the one file to rename first")
        elif len(files) > 1:
            self.status_bar.showMessage(
                f"Rename File renames one file; {len(files)} are selected (use Rename / Export / Move for several)"
            )
        elif files[0].load_error:
            self.status_bar.showMessage(f"Can't rename {files[0].path.name}: it failed to load")
        else:
            self.rename_single_file(files[0])

    # --- TagPanel collapse/restore ----------------------------------------
    # New: this project never had a way to minimize the panel before --
    # resize logic lives in redactor_common's SplitterPaneCollapser
    # (shared with epub), same split of responsibility as there: the
    # panel only ever asks to be toggled, MainWindow owns the splitter.

    def _toggle_tag_panel(self) -> None:
        self._panel_collapser.toggle()
        self._sync_tag_panel_collapsed_indicator()

    def _on_splitter_moved(self, _pos, _index) -> None:
        """Keeps the panel's own toggle-button glyph in sync when the
        user drags the splitter handle by hand, not just when they use
        the button/toolbar action."""
        self._sync_tag_panel_collapsed_indicator()

    def _sync_tag_panel_collapsed_indicator(self) -> None:
        collapsed = self._panel_collapser.is_collapsed()
        self.tag_panel.set_collapsed_indicator(collapsed)
        # View > Show Metadata Panel mirrors the panel, whoever moved it.
        shown = self.actions_by_key["show_metadata_panel"]
        shown.blockSignals(True)
        shown.setChecked(not collapsed)
        shown.blockSignals(False)

    def _on_show_panel_toggled(self, checked: bool = True) -> None:
        """View > Show Metadata Panel: ticked = panel visible."""
        if checked == self._panel_collapser.is_collapsed():
            self._toggle_tag_panel()

    def _update_play_button(self, selected: list[VideoFile]) -> None:
        """The side panel's Play button: on only for exactly one selected file that loaded without error."""
        self.tag_panel.play_button.setEnabled(len(selected) == 1 and not selected[0].load_error)

    def _on_selection_changed(self) -> None:
        selected = self._selected_video_files()
        self._update_play_button(selected)
        set_apply_count(self.apply_action, len(selected))  # 'Apply to N Selected', greyed at 0
        if not selected:
            self.tag_panel.set_preview_image(None)
            return
        merged = self._merge_metadata_for_panel(selected)
        self.tag_panel.load_values(merged)
        self._update_preview(selected)

    def _update_preview(self, selected: list[VideoFile]) -> None:
        """Show a thumbnail for a single selection; a neutral placeholder
        for multi-selection, since there's no single frame that
        represents several different files.

        The first preview of a file runs ffmpeg to grab a frame. That
        used to happen right here on the GUI thread, so selecting a large
        or slow-to-seek file (or one on a slow network share) froze the
        window until ffmpeg finished. It now runs on a worker thread via
        redactor_common's AsyncPreviewLoader: debounced, so arrowing
        through the table doesn't start an ffmpeg per row, and only the
        latest selection's result is ever shown. Later selections of the
        same file still hit VideoFile's on-disk thumbnail cache.
        """
        if len(selected) != 1:
            self._preview_loader.cancel()
            self.tag_panel.set_preview_image(None)
            self.tag_panel.preview_label.setText(f"{len(selected)} files selected")
            return

        vf = selected[0]
        if vf.load_error:
            self._preview_loader.cancel()
            self.tag_panel.set_preview_image(None)
            return

        self.tag_panel.set_preview_loading()
        self._preview_loader.request(vf, vf.get_thumbnail)

    def _on_preview_ready(self, _vf: VideoFile, image: QImage) -> None:
        self.tag_panel.set_preview_qimage(image)

    def _merge_metadata_for_panel(self, files: list[VideoFile]) -> dict[str, object]:
        """Merge selected files' fields for the panel: a field with the
        same value across all selected files shows that value; a field
        that differs shows as unset (None), same "don't show a fake
        single value for a mixed selection" spirit as the epub tool's
        <multiple values> handling. Exact "mixed" display polish left for
        the functional-testing pass -- this establishes the merge logic.
        """
        merged: dict[str, object] = {}
        for field_name in EDITABLE_FIELDS:
            values = {getattr(f.metadata, field_name, None) for f in files}
            merged[field_name] = values.pop() if len(values) == 1 else None
        return merged

    # --- Saving ------------------------------------------------------------

    def _on_save_all(self) -> None:
        # "Hit Save" implies "I meant to Apply first" -- flush any
        # typed-but-not-yet-Applied panel edits onto the selected file(s)
        # before saving, so Save never silently writes stale data just
        # because Apply wasn't clicked separately. Done before computing the
        # dirty list, since applying pending panel changes can itself be what
        # makes a file dirty.
        self.tag_panel.apply_pending_changes()

        dirty = [vf for vf in self.video_files if vf.dirty]
        if not dirty:
            self.status_bar.showMessage("Nothing to save -- no unsaved changes")
            return
        saveable, skipped = self._split_saveable(dirty)
        if not saveable:
            self.status_bar.showMessage(
                f"Nothing to save -- all {len(skipped)} changed file(s) have load errors"
            )
            return
        self._save_files(saveable, skipped_error_count=len(skipped))

    def _split_saveable(self, files: list[VideoFile]) -> tuple[list[VideoFile], list[VideoFile]]:
        """Split into (saveable, skipped). A file with a load_error is
        never attempted -- we don't have a trustworthy read of what's
        actually on disk for it, so writing on top of that is refused
        rather than risking a bad file getting worse. Files aren't
        silently dropped: the caller reports how many were skipped."""
        saveable = [vf for vf in files if not vf.load_error]
        skipped = [vf for vf in files if vf.load_error]
        return saveable, skipped

    def _save_files(self, files: list[VideoFile], skipped_error_count: int = 0) -> None:
        """Save the given files, reporting success/failure counts.

        Every file in `files` is attempted (no skip-on-first-error) so one
        bad file doesn't block the rest of the batch from saving -- same
        reasoning as the epub tool's per-book save-error tracking rather
        than an all-or-nothing batch.

        Shows redactor_common's shared run_with_progress helper -- each
        vf.save() now does a write PLUS an immediate verify-read-back
        (see core/video_file.py), roughly doubling the I/O cost per
        file since that safety net was added, which made a multi-file
        save look exactly as "frozen" as a heavy folder load used to
        before a dialog existed here. Threshold is now a plain item
        count (3+), matching mp3/cbz's own save dialogs, rather than
        this project's previous bespoke setMinimumDuration(400) -- one
        less "arrived at independently" divergence between the family's
        save flows.
        """
        succeeded = 0
        failed: list[VideoFile] = []

        def _step(vf: VideoFile, _index: int) -> None:
            nonlocal succeeded
            vf.save()
            if vf.save_error:
                failed.append(vf)
            else:
                succeeded += 1

        cancelled = not run_with_progress(
            self, files, _step, "Saving files...",
            threshold=3,
            label_for=lambda vf: f"Saving {vf.path.name}...",
        )

        self._refresh_table_rows()

        skip_note = f", {skipped_error_count} skipped (load errors)" if skipped_error_count else ""
        not_attempted = len(files) - succeeded - len(failed)
        cancel_note = f", cancelled -- {not_attempted} file(s) not yet attempted" if cancelled else ""

        journal_note = _journal_problem_note()
        if failed:
            msg = f"Saved {succeeded} file(s), {len(failed)} failed{skip_note}{cancel_note}{journal_note}"
            self.status_bar.showMessage(msg)
            details = _error_details([f"{vf.path.name}: {vf.save_error}" for vf in failed])
            QMessageBox.warning(self, "Some files failed to save", details)
        else:
            self.status_bar.showMessage(f"Saved {succeeded} file(s){skip_note}{cancel_note}{journal_note}")

    def _count_dirty(self) -> int:
        return sum(1 for vf in self.video_files if vf.dirty)

    def _confirm_discard(self, action_desc: str) -> bool:
        reply = QMessageBox.question(
            self,
            "Unsaved changes",
            f"You have unsaved changes. Are you sure you want to {action_desc}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def closeEvent(self, event) -> None:
        if self._count_dirty() and not self._confirm_discard("quit without saving"):
            event.ignore()
            return
        # A conversion still running (Redact's convert step, Media > Convert to MP4/MKV (rules)) must not keep
        # ffmpeg encoding for hours after the window is gone: tell the run to stop and kill the process.
        active = getattr(self, "_active_cancel", None)
        if active is not None:
            active.set()
        kill_running_ffmpeg()
        super().closeEvent(event)

    # --- Applying bulk edits ----------------------------------------------

    def _on_apply_menu(self) -> None:
        """Edit > Apply to N Selected (Ctrl+Return, toolbar): applies the panel's typed changes, or says why
        there is nothing to do (it used to return silently)."""
        if not self.tag_panel.has_pending_changes():
            self.status_bar.showMessage("Nothing to apply: no field in the panel was changed")
            return
        self.tag_panel.apply_pending_changes()

    def _on_apply_to_selected(self, changed_fields: dict[str, object]) -> None:
        if not changed_fields:
            # Nothing was actually touched -- marking every selected
            # file dirty anyway (the previous behavior here) would
            # falsely show files as having unsaved changes when
            # nothing was ever applied to them. Real bug, caught while
            # moving the Apply button to the toolbar and reconsidering
            # what triggering it with nothing pending should do.
            self.status_bar.showMessage("Nothing to apply: no field in the panel was changed")
            return
        selected = self._selected_video_files()
        if not selected:
            self.status_bar.showMessage("Nothing to apply to: select the file(s) first")
            return
        self._push_undo("Bulk Edit", selected)
        for vf in selected:
            for field_name, value in changed_fields.items():
                if field_name == "content_type" and value:
                    value = ContentType(value)
                setattr(vf.metadata, field_name, value)
            vf.dirty = True
        self._refresh_table_rows()
        self.status_bar.showMessage(
            f"Applied {len(changed_fields)} field(s) to {len(selected)} file(s) -- not yet saved to disk"
        )

    # --- TMDB import ---------------------------------------------------

    def _on_import_tmdb(self, mode: str) -> None:
        """Import metadata from TMDB for every selected file -- batch-
        capable, with different strategies per mode since the realistic
        use case differs:

        Movie mode: each selected file is presumed to be a DIFFERENT
        movie (the common case for a movies folder), so this shows the
        search+picker dialog fresh for EACH file in turn -- batch
        PROCESSING (sequential per-file confirmation), not one match
        applied to every file. Canceling for one file just skips it.

        TV mode: multiple selected files typically belong to the SAME
        show (e.g. a season's worth of episodes), so the show is
        searched and confirmed ONCE for the whole selection, and
        show-level fields (network, genre, overview, poster) apply to
        every selected file immediately. Episode-specific fields still
        need per-file confirmation via the episode picker, looped
        afterward one file at a time, since each episode genuinely is
        different -- but at least the tedious "search the same show
        over and over" step only happens once now.
        """
        selected = self._selected_video_files()
        if not selected:
            QMessageBox.information(self, "No Files Selected", "Select at least one file first.")
            return

        loadable = [vf for vf in selected if not vf.load_error]
        skipped_load_errors = len(selected) - len(loadable)
        if not loadable:
            QMessageBox.warning(
                self, "Cannot Import",
                "All selected files failed to load -- fix that first.",
            )
            return

        if mode == "movie":
            self._import_tmdb_movies(loadable, skipped_load_errors)
        else:
            self._import_tmdb_tv(loadable, skipped_load_errors)

    def _import_tmdb_movies(self, files: list, skipped_load_errors: int) -> None:
        imported = 0
        skipped_no_match = 0
        poster_saved = 0
        fetch_failures: list[tuple] = []
        overwrite_choice: dict = {}  # "Replace existing poster?" answer, kept across the batch

        # One undo entry for the whole batch (the stack keeps only the
        # last few entries, so one per file would lose the earlier ones).
        self._push_undo("TMDB Import", files)
        for vf in files:
            guess = parse_release_name(vf.path.stem)
            dialog = TMDBSearchDialog(
                mode="movie", initial_query=guess.title, initial_year=guess.year or "",
                parent=self,
            )
            if not dialog.exec() or dialog.selected_candidate is None:
                skipped_no_match += 1
                continue
            candidate = dialog.selected_candidate

            try:
                details = run_lookup(self, get_movie_details, candidate.tmdb_id)
            except TMDBError as e:
                fetch_failures.append((vf, str(e)))
                continue

            vf.metadata.content_type = ContentType.MOVIE
            poster_path = details.pop("_poster_path", None)
            for field_name, value in details.items():
                setattr(vf.metadata, field_name, value)
            vf.dirty = True
            imported += 1

            if poster_path:
                try:
                    image_bytes = run_lookup(self, download_poster, poster_path)
                except TMDBError as e:
                    # A poster failure doesn't undo the metadata import
                    # that already succeeded -- collected alongside
                    # fetch failures for the end-of-batch summary rather
                    # than interrupting the loop with its own dialog.
                    fetch_failures.append((vf, f"poster download failed: {e}"))
                else:
                    status, detail = self._save_sidecar(
                        lambda overwrite: vf.save_poster_sidecar(image_bytes, overwrite), overwrite_choice,
                    )
                    if status == "saved":
                        poster_saved += 1
                    elif status == "error":
                        fetch_failures.append((vf, f"poster not saved: {detail}"))

        self._refresh_table_rows()
        if self._selected_video_files():
            self._on_selection_changed()

        parts = [f"Imported TMDB metadata for {imported} file(s)"]
        if poster_saved:
            parts.append(f"{poster_saved} poster(s) saved")
        if skipped_no_match:
            parts.append(f"{skipped_no_match} skipped (no match confirmed)")
        if skipped_load_errors:
            parts.append(f"{skipped_load_errors} skipped (load errors)")
        if fetch_failures:
            parts.append(f"{len(fetch_failures)} failed")
        self.status_bar.showMessage(", ".join(parts))

        if fetch_failures:
            details_text = _error_details([f"{vf.path.name}: {err}" for vf, err in fetch_failures])
            QMessageBox.warning(self, "Some files failed to import", details_text)

    def _import_tmdb_tv(self, files: list, skipped_load_errors: int) -> None:
        guess = parse_release_name(files[0].path.stem)
        dialog = TMDBSearchDialog(mode="tv", initial_query=guess.title, parent=self)
        if not dialog.exec() or dialog.selected_candidate is None:
            return
        candidate = dialog.selected_candidate

        try:
            show_details = run_lookup(self, get_tv_show_details, candidate.tmdb_id)
        except TMDBError as e:
            QMessageBox.warning(self, "TMDB Fetch Failed", str(e))
            return

        poster_path = show_details.pop("_poster_path", None)
        poster_bytes = None
        poster_error = ""
        if poster_path:
            try:
                poster_bytes = run_lookup(self, download_poster, poster_path)
            except TMDBError as e:
                poster_error = str(e)

        posters_saved = 0
        overwrite_choice: dict = {}  # "Replace existing poster?" answer, kept across the batch
        self._push_undo("TMDB Import", files)
        for vf in files:
            vf.metadata.content_type = ContentType.TV
            for field_name, value in show_details.items():
                setattr(vf.metadata, field_name, value)
            vf.dirty = True
            if poster_bytes:
                status, detail = self._save_sidecar(
                    lambda overwrite: vf.save_poster_sidecar(poster_bytes, overwrite), overwrite_choice,
                )
                if status == "saved":
                    posters_saved += 1
                elif status == "error":
                    poster_error = f"{vf.path.name}: {detail}"

        # Episode-level details still need per-file confirmation --
        # each episode genuinely is different, so this deliberately
        # doesn't try to skip the picker even for files that already
        # have season/episode numbers set from some other source (e.g.
        # Parse Filename to Metadata run earlier) -- matching this
        # project's consistent "always confirm explicitly, never
        # auto-apply" principle for anything that writes to a file.
        episode_failures: list[tuple] = []
        episodes_set = 0
        for vf in files:
            # Parsed per-file (not reused from the show-level `guess`
            # above) since each episode's own filename is what carries
            # its season/episode number -- e.g. "Show S02E04.mkv".
            file_guess = parse_release_name(vf.path.stem)
            episode_dialog = TVEpisodePickerDialog(
                candidate.tmdb_id, initial_season=file_guess.season,
                initial_episode=file_guess.episode, parent=self,
            )
            if not episode_dialog.exec() or episode_dialog.selected_episode is None:
                continue
            try:
                episode_details = run_lookup(
                    self, get_tv_episode_details, candidate.tmdb_id, episode_dialog.selected_season,
                    episode_dialog.selected_episode.episode_number,
                )
            except TMDBError as e:
                episode_failures.append((vf, str(e)))
                continue
            for field_name, value in episode_details.items():
                setattr(vf.metadata, field_name, value)
            episodes_set += 1

        self._refresh_table_rows()
        if self._selected_video_files():
            self._on_selection_changed()

        parts = [f"Imported \"{candidate.name}\" show-level metadata for {len(files)} file(s)", f"{episodes_set} episode(s) matched"]
        if posters_saved:
            parts.append(f"{posters_saved} poster(s) saved")
        if poster_error:
            parts.append(f"poster problem: {poster_error}")
        if skipped_load_errors:
            parts.append(f"{skipped_load_errors} skipped (load errors)")
        if episode_failures:
            parts.append(f"{len(episode_failures)} episode lookup(s) failed")
        self.status_bar.showMessage(", ".join(parts))

        if episode_failures:
            details_text = _error_details([f"{vf.path.name}: {err}" for vf, err in episode_failures])
            QMessageBox.warning(self, "Some episode lookups failed", details_text)

    # --- IMDb (local database) import ---------------------------------------

    def _imdb_database_path(self) -> str:
        """The local IMDb database to use; with none set up it offers
        Tools > IMDb Database... first. "" when there is nothing to use."""
        from core import imdb_local, imdb_settings
        from core.imdb_import import ImdbDatabaseError

        name = "IMDb (Local Database)"
        path = imdb_settings.load_database()
        if not path or not os.path.isfile(path):
            reply = QMessageBox.question(
                self, name,
                "No local IMDb database is set up yet. It is built from IMDb's free datasets, which you download "
                "yourself (personal, non-commercial use only).\n\nOpen Tools > IMDb Database... to set it up?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._on_open_imdb_settings()
            path = imdb_settings.load_database()
            if not path or not os.path.isfile(path):
                return ""
        try:
            imdb_local.open_database(path)  # fail here, with the file's own message, not once per file
        except ImdbDatabaseError as exc:
            QMessageBox.warning(self, name, f"{exc}\n\nCheck Tools > IMDb Database...")
            return ""
        return path

    def _on_import_imdb_local(self) -> None:
        """Metadata > Look Up > IMDb (Local Database): like the TMDB import, but against the
        offline database. Every file is confirmed by hand: a search dialog (Film or TV show,
        started from the filename) and, for a show, the season and episode picker. The
        datasets have no plot, poster or cast; only title, year, genres and episode data are set."""
        selected = self._selected_video_files()
        if not selected:
            QMessageBox.information(self, "No Files Selected", "Select at least one file first.")
            return
        loadable = [vf for vf in selected if not vf.load_error]
        skipped_load_errors = len(selected) - len(loadable)
        if not loadable:
            QMessageBox.warning(self, "Cannot Import", "All selected files failed to load -- fix that first.")
            return
        path = self._imdb_database_path()
        if path:
            self._import_imdb(loadable, skipped_load_errors, path)

    def _import_imdb(self, files: list, skipped_load_errors: int, path: str) -> None:
        from redactor_common.core.local_db import normalize_words

        from core import imdb_import, imdb_local
        from core.imdb_import import ImdbDatabaseError
        from gui.imdb_episode_picker_dialog import ImdbEpisodePickerDialog

        db = imdb_local.open_database(path)
        source = SearchSource(
            name="IMDb (Local Database)",
            movies=lambda query, year=None: imdb_local.search_movies(db, query, year),
            tv=lambda query, year=None: imdb_local.search_series(db, query, year),
            errors=(ImdbDatabaseError,),
            note="Searches your offline IMDb database. IMDb's datasets have no plot, poster or cast: only the "
                 "title, year, genres and (for shows) episode data are filled in.\n\n" + imdb_import.ATTRIBUTION,
            switchable=True,
        )
        imported = episodes_set = skipped_no_match = 0
        shows: dict[str, object] = {}  # a show confirmed once is reused for the rest of the batch
        self._push_undo("IMDb Import", files)
        for vf in files:
            guess, tv_title, tv_year = _tv_guess(vf.path.stem)  # a show's "(2005)" is split off its title
            is_tv = guess.kind == "tv" or vf.metadata.content_type is ContentType.TV
            title, year = (tv_title, tv_year) if guess.kind == "tv" else (guess.title, guess.year or "")
            candidate = shows.get(normalize_words(title)) if is_tv else None
            if candidate is None:
                dialog = TMDBSearchDialog(
                    mode="tv" if is_tv else "movie", initial_query=title, initial_year=year,
                    parent=self, source=source,
                )
                if not dialog.exec() or dialog.selected_candidate is None:
                    skipped_no_match += 1
                    continue
                candidate = dialog.selected_candidate
            if isinstance(candidate, imdb_local.ImdbTVCandidate):
                shows[normalize_words(title)] = shows[normalize_words(candidate.name)] = candidate
                vf.metadata.content_type = ContentType.TV
                self._apply_imdb_fields(vf, imdb_local.show_fields(candidate))
                episode_dialog = ImdbEpisodePickerDialog(
                    path, candidate.tconst, candidate.name, initial_season=vf.metadata.season_number or guess.season,
                    initial_episode=vf.metadata.episode_number or guess.episode, parent=self,
                )
                if episode_dialog.exec() and episode_dialog.selected_episode is not None:
                    self._apply_imdb_fields(vf, imdb_local.episode_fields(episode_dialog.selected_episode), replace_date=True)
                    episodes_set += 1
            else:
                vf.metadata.content_type = ContentType.MOVIE
                self._apply_imdb_fields(vf, imdb_local.movie_fields(candidate), replace_date=True)
            vf.dirty = True
            imported += 1

        self._refresh_table_rows()
        if self._selected_video_files():
            self._on_selection_changed()
        parts = [f"Imported IMDb metadata for {imported} file(s)"]
        if episodes_set:
            parts.append(f"{episodes_set} episode(s) matched")
        if skipped_no_match:
            parts.append(f"{skipped_no_match} skipped (no match confirmed)")
        if skipped_load_errors:
            parts.append(f"{skipped_load_errors} skipped (load errors)")
        self.status_bar.showMessage(", ".join(parts))

    @staticmethod
    def _apply_imdb_fields(vf, fields: dict, replace_date: bool = False) -> None:
        """Writes the confirmed IMDb match into the file's metadata. IMDb's dates are a bare year, so
        an existing release date that is in the same year (usually a fuller date) is kept."""
        for name, value in fields.items():
            if name == "release_date":
                current = vf.metadata.release_date or ""
                if current and (current[:4] == str(value)[:4] or not replace_date):
                    continue
            setattr(vf.metadata, name, value)

    def _apply_tvdb_episode_details(self, tvdb_id: int, details: dict, filename_stem: str = "") -> None:
        """Follow-up step after a TVDB show match: prompt for season +
        episode, then merge episode-level fields into `details` in
        place. Deliberate partial-import-on-cancel behavior -- if the
        user cancels the episode picker, `details` is left as
        show-level-only rather than aborting the whole import. Uses
        TVDBEpisodePickerDialog (which fetches all episodes once, no
        per-season network call) rather than the TMDB picker.

        `filename_stem`, when given, is parsed for a season/episode
        number to pre-select in the picker (see core.release_name_parser).
        """
        file_guess = parse_release_name(filename_stem)
        episode_dialog = TVDBEpisodePickerDialog(
            tvdb_id, initial_season=file_guess.season,
            initial_episode=file_guess.episode, parent=self,
        )
        if not episode_dialog.exec() or episode_dialog.selected_episode is None:
            return

        try:
            episode_details = run_lookup(
                self, get_episode_details, tvdb_id, episode_dialog.selected_season,
                episode_dialog.selected_episode.episode_number,
            )
        except TVDBError as e:
            QMessageBox.warning(self, "Could Not Load Episode Details", str(e))
            return

        details.update(episode_details)

    def _on_import_tvdb(self) -> None:
        """Import metadata from TheTVDB for exactly one selected file --
        TV only, TheTVDB's role in this app (TMDB remains the source
        for movies).

        NOTE: still single-file only, unlike _on_import_tmdb's TV mode
        (which now batch-imports across a whole selection -- search the
        show once, apply show-level fields to every selected file, then
        loop the episode picker per file). TVDB wasn't part of the
        specific request that prompted that change and hasn't been
        updated to match yet -- a real, known gap, not an oversight to
        be silent about.
        """
        selected = self._selected_video_files()
        if len(selected) != 1:
            QMessageBox.information(
                self, "Select One File",
                "TheTVDB import works on one file at a time for now. Select exactly one row.",
            )
            return

        vf = selected[0]
        if vf.load_error:
            QMessageBox.warning(
                self, "Cannot Import",
                f"{vf.path.name} failed to load ({vf.load_error}) -- fix that first.",
            )
            return

        guess = parse_release_name(vf.path.stem)

        dialog = TVDBSearchDialog(initial_query=guess.title, parent=self)
        if not dialog.exec() or dialog.selected_candidate is None:
            return
        candidate = dialog.selected_candidate

        # Nothing is touched (no undo entry, no content type change)
        # until the fetch has succeeded.
        try:
            details = run_lookup(self, get_series_details, candidate.tvdb_id)
            self._apply_tvdb_episode_details(candidate.tvdb_id, details, vf.path.stem)
        except TVDBError as e:
            QMessageBox.warning(self, "TheTVDB Fetch Failed", str(e))
            return

        self._push_undo("TheTVDB Import", [vf])
        vf.metadata.content_type = ContentType.TV
        poster_url = details.pop("_poster_path", None)
        for field_name, value in details.items():
            setattr(vf.metadata, field_name, value)
        vf.dirty = True

        status_msg = "Imported metadata from TheTVDB -- not yet saved to disk"
        if "episode_number" not in details:
            status_msg += " (show-level only -- episode selection was skipped)"
        if poster_url:
            try:
                image_bytes = run_lookup(self, download_image, poster_url)
            except TVDBError as e:
                status_msg += f"; poster download failed: {e}"
            else:
                status, detail = self._save_sidecar(lambda overwrite: vf.save_poster_sidecar(image_bytes, overwrite), {})
                if status == "saved":
                    status_msg += f"; poster saved as {detail.name}"
                elif status == "kept":
                    status_msg += "; existing poster kept"
                else:
                    status_msg += f"; poster not saved: {detail}"

        self._refresh_table_rows()
        if any(selected_vf is vf for selected_vf in self._selected_video_files()):
            self._on_selection_changed()
        self.status_bar.showMessage(status_msg)

    def _save_sidecar(self, write, choice: dict) -> tuple[str, object]:
        """Runs `write(overwrite)` (VideoFile.save_poster_sidecar /
        save_subtitle_sidecar) and reports the outcome as ("saved",
        path), ("kept", None) -- the file exists and the user declined to
        replace it -- or ("error", message) for an OSError. Never
        replaces an existing file unasked; `choice` carries a "to all"
        answer ({"all": True/False}) across a batch."""
        try:
            return "saved", write(bool(choice.get("all")))
        except FileExistsError as exc:
            if choice.get("all") is False:
                return "kept", None
            name = os.path.basename(exc.filename) if exc.filename else "The file"
            button = QMessageBox.StandardButton
            answer = QMessageBox.question(
                self, "Replace Existing File?", f"{name} already exists. Replace it?",
                button.Yes | button.YesToAll | button.No | button.NoToAll, button.No,
            )
            if answer == button.NoToAll:
                choice["all"] = False
            if answer not in (button.Yes, button.YesToAll):
                return "kept", None
            if answer == button.YesToAll:
                choice["all"] = True
            try:
                return "saved", write(True)
            except OSError as retry_exc:
                return "error", str(retry_exc)
        except OSError as exc:
            return "error", str(exc)

    # --- Remux ---------------------------------------------------------

    def _on_check_files(self) -> None:
        """Media > Check Files...: the quick health check for the
        selected files (all loaded files when none are selected), then
        the results and an optional lossless Repair -- see
        gui/file_check_dialog.py and core/file_check.py."""
        from gui.file_check_dialog import run_check_and_repair

        files = self._selected_video_files() or self._visible_video_files()
        if not files:
            QMessageBox.information(self, "Check Files", OPEN_FILES_FIRST)
            return
        run_check_and_repair(self, files, _error_details)

    def _on_find_duplicates(self) -> None:
        """Media > Find Duplicates...: groups the loaded files that
        look like the same video, for review only -- see
        gui/duplicates_dialog.py and core/video_duplicates.py."""
        from gui.duplicates_dialog import find_duplicates_flow

        if len(self.video_files) < 2:
            QMessageBox.information(self, "Find Duplicates", "Open at least two video files first (File > Open Files or Open Folder).")
            return
        find_duplicates_flow(self, list(self.video_files))

    # --- Redact ---------------------------------------------------------

    CONVERT_DRY_RUN_LABEL = "Dry run the conversion step (list what would happen, change nothing)"

    def _redact_targets(self) -> tuple[list[VideoFile], Optional[bool]]:
        """The files to redact and this run's convert dry-run choice (None when the recipe's convert step is
        off). The selected files, else -- after asking -- every loaded file (Redact is meant to be one click on
        a whole folder, so nothing selected is not an error). When the convert step is on, the confirm (also
        shown for a selection then) carries a "Dry run the conversion step" checkbox, ticked by default: the
        choice is made per run, never by a stale setting."""
        targets = self._selected_video_files()
        convert_on = self._redact_convert_on()
        if targets and not convert_on:
            return targets, None
        if not targets and not self.video_files:
            QMessageBox.information(self, "Redact", OPEN_FILES_FIRST)
            return [], None
        if targets:
            title, question = "Redact", f"Redact the {len(targets)} selected file(s)?"
        else:
            targets = self._visible_video_files()  # the files a column filter shows (all loaded ones when none is active)
            if not targets:
                QMessageBox.information(self, "Redact", "No files are shown: the column filters hide them all.")
                return [], None
            if len(targets) == len(self.video_files):
                title, question = "Redact all files?", f"Nothing is selected. Redact all {len(targets)} loaded file(s)?"
            else:
                title, question = "Redact the shown files?", (
                    f"Nothing is selected. Redact the {len(targets)} file(s) shown by the column filters "
                    f"({len(self.video_files) - len(targets)} hidden file(s) are left alone)?"
                )
        if not convert_on:
            answer = QMessageBox.question(
                self, title, question + "\n\n" + self._redact_fate_text(None),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            )
            return (targets, None) if answer == QMessageBox.StandardButton.Yes else ([], None)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(title)
        box.setText(
            question + "\n\n" + self._redact_fate_text(False)
            + "\n\nThe recipe's conversion step is on. With the box below ticked it only lists what it would convert."
        )
        dry_box = QCheckBox(self.CONVERT_DRY_RUN_LABEL)
        dry_box.setChecked(True)
        box.setCheckBox(dry_box)
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return [], None
        return targets, dry_box.isChecked()

    def _redact_convert_on(self) -> bool:
        """Whether the saved Redact recipe has the convert step (convert_library) on."""
        recipe = load_recipe(build_catalogue(sample=self._redact_sample_values))
        return bool(recipe.enabled.get("convert_library"))

    def _redact_fate_text(self, convert_dry_run: Optional[bool]) -> str:
        """What happens to originals in THIS run: depends on whether the recipe's convert step is on, this run's
        dry-run choice, and the Library safety settings (quarantine folder, delete-after-conversion)."""
        convert_on = self._redact_convert_on()
        return original_fate_text(
            library_safety.get_safety_settings(), convert_on, dry_run=bool(convert_dry_run) if convert_on else None,
        )

    def _on_redact(self) -> None:
        """Edit > Redact (Ctrl+Shift+E, toolbar): runs the saved recipe
        (core/redact_steps.py) on the targets through redactor_common's
        engine -- progress, Cancel, then the report with Needs review. A
        file with unsaved edits or a load error is skipped and named in the
        report, never overwritten. Redact isn't on the Undo stack (the
        Recycle Bin copy of each original is the undo), so the stack is
        cleared rather than left pointing at old states."""
        targets, convert_dry_run = self._redact_targets()
        if not targets:
            return
        unsaved = [vf for vf in targets if vf.dirty and not vf.stamp_only_dirty]
        if unsaved and QMessageBox.question(
            self, "Unsaved changes",
            f"{len(unsaved)} of the {len(targets)} file(s) have unsaved edits. Redact works on saved "
            "files, so those will be skipped (and listed in the report). Continue with the rest?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Yes:
            return

        cancel_event = threading.Event()  # Cancel (and closing the app) stops a running ffmpeg encode
        self._active_cancel = cancel_event
        # No `background` hook here: redactor_common's run_redact runs every file on a worker thread now, and
        # that helper only works ON the GUI thread, so the steps call their slow work directly.
        # convert_dry_run is a bool whenever the convert step is on (asked above), never the old saved setting.
        env = RedactEnv(
            rename_log=_rename_log(), imdb_local=imdb_settings.load_database(), cancel_event=cancel_event,
            convert_dry_run=convert_dry_run,
        )
        catalogue = build_catalogue(sample=self._redact_sample_values)
        fate = self._redact_fate_text(convert_dry_run)
        try:
            report = run_redact_dialog(
                self, targets, load_recipe(catalogue), catalogue,
                make_context=lambda vf: VideoCtx(vf, env),
                describe=lambda vf: vf.path.name,
                title="Redact", show_results=False,
                finalize=finalize_file, finalize_label="Save",
                on_cancel=cancel_event.set,
            )
        finally:
            self._active_cancel = None
        self._clear_undo()
        self._after_batch_edit("Redact finished" + _journal_problem_note())
        if report is not None:
            RedactResultsDialog(
                report, self, title="Redact results",
                header=fate + " A rename or move is also listed under File > Undo Last Rename.",
            ).exec()

    def _on_edit_redact_recipe(self) -> None:
        """Edit > Edit Redact Recipe...: the shared recipe editor over
        this app's steps; the result is stored in the settings file."""
        catalogue = build_catalogue(sample=self._redact_sample_values)
        recipe = load_recipe(catalogue)
        if not recipe_is_saved():
            # First save: pre-fill each pattern with the one in effect now, so
            # pressing OK keeps it even if Rename/Export changes later.
            recipe = pin_patterns(recipe, catalogue)
        dialog = RecipeEditorDialog(catalogue, recipe, self)
        if dialog.exec():
            save_recipe(dialog.recipe())

    def _redact_sample_values(self) -> dict[str, str] | None:
        """Placeholder values of the first loaded video, for the recipe
        editor's pattern previews (None: the built-in sample)."""
        if not self.video_files:
            return None
        return placeholder_values(self.video_files[0].metadata)

    def _on_remux_selected(self) -> None:
        """Remux selected MKV files to MP4 (batch-capable, -c copy so
        it's lossless -- no re-encode), on a worker thread with a
        progress dialog and Cancel. ONE confirmation before the run (its
        checkbox: move the originals of the verified results -- every track
        kept, verify_remux -- to the Recycle Bin), ONE summary after it with
        every per-file detail under Show Details; no box per file. The new
        MP4s are read back under a progress dialog and added as rows. A
        result that lost something is added but its original is always kept.
        A recycled original's Tools > History entries follow its MP4.
        """
        selected = self._selected_video_files()
        mkv_files = [vf for vf in selected if vf.is_mkv]
        non_mkv_count = len(selected) - len(mkv_files)

        if not mkv_files:
            QMessageBox.information(
                self, "Nothing to Remux",
                "Select at least one MKV file to remux to MP4.",
            )
            return

        jobs: list[tuple[Path, Path]] = []
        job_files: list[VideoFile] = []
        skipped_existing: list[VideoFile] = []
        claimed: set[str] = set()
        for vf in mkv_files:
            output_path = vf.path.with_suffix(".mp4")
            # Refuse to overwrite an existing file (or one another job of
            # this batch is about to write) rather than guess whether
            # it's unrelated or a leftover from a prior remux -- same
            # "deliberate action, fail clearly" reasoning as
            # rename_book_file's collision handling in the epub tool.
            if not _claim_output(output_path, claimed):
                skipped_existing.append(vf)
                continue
            jobs.append((vf.path, output_path))
            job_files.append(vf)

        recycle = False
        if jobs:
            # ONE confirmation before the run (with the Recycle Bin choice), ONE summary after it, no modal per file.
            choice = self._confirm_remux(jobs)
            if choice is None:
                return
            recycle = choice
        results = (
            self._run_transcode_jobs(jobs, None, "Remuxing to MP4", remux=True) if jobs else {}
        )

        succeeded: list[tuple[VideoFile, Path]] = []
        failed: list[tuple[VideoFile, str]] = []
        unverified: list[tuple[VideoFile, str]] = []
        verified: list[tuple[VideoFile, Path]] = []  # every track kept: the original may go
        cancelled_count = 0
        deletion_failures: list[tuple[VideoFile, str]] = []
        removed_originals: list[VideoFile] = []

        for i, vf in enumerate(job_files):
            output_path = jobs[i][1]
            result = results.get(i)
            if result is None:
                cancelled_count += 1  # never started -- batch was cancelled first
                continue
            ok, message = result
            if not ok:
                if message == "Cancelled":
                    cancelled_count += 1
                else:
                    failed.append((vf, message.strip() or "ffmpeg remux failed"))
                continue
            succeeded.append((vf, output_path))
            if message:
                # Something didn't make it into the MP4 (or it couldn't
                # be compared): keep the original, don't even offer.
                unverified.append((vf, message))
            else:
                verified.append((vf, output_path))

        # Each new MP4 is read (ffprobe + tags) under a progress dialog, not frozen on the GUI thread.
        new_files = self._load_results([out for _vf, out in succeeded], "Reading the remuxed files...")

        # The Recycle Bin choice was made in the confirmation before the run; only verified results qualify.
        if verified and recycle:
            def trash_one(item: tuple[VideoFile, Path], _index: int) -> None:
                vf, output = item
                try:
                    move_to_trash(str(vf.path))
                    removed_originals.append(vf)
                except (TrashError, OSError) as e:
                    deletion_failures.append((vf, str(e)))
                    return
                # The MKV is gone and the MP4 is its successor: its Tools > History entries follow it.
                history.record_move(vf.path, output)

            run_with_progress(
                self, verified, trash_one, "Moving originals to the Recycle Bin...", threshold=3,
                label_for=lambda item: f"Recycling {item[0].path.name}",
            )

        self.video_files.extend(new_files)
        if removed_originals:
            removed_set = set(id(vf) for vf in removed_originals)
            self.video_files = [vf for vf in self.video_files if id(vf) not in removed_set]

        self._refresh_table_rows()

        parts = [f"Remuxed {len(succeeded)} file(s)"]
        if failed:
            parts.append(f"{len(failed)} failed")
        if cancelled_count:
            parts.append(f"{cancelled_count} cancelled")
        if skipped_existing:
            parts.append(f"{len(skipped_existing)} skipped (MP4 already exists)")
        if unverified:
            parts.append(f"{len(unverified)} original(s) kept (result incomplete)")
        if non_mkv_count:
            parts.append(f"{non_mkv_count} non-MKV selection(s) ignored")
        if deletion_failures:
            parts.append(f"{len(deletion_failures)} original(s) could not be moved to the Recycle Bin")
        summary = ", ".join(parts)
        self.status_bar.showMessage(summary)

        # ONE end-of-run summary: failures, kept originals and every other note under Show Details.
        removed_ids = {id(vf) for vf in removed_originals}
        lines = [f"FAILED {vf.path.name}: {err}" for vf, err in failed]
        lines += [f"ORIGINAL KEPT {vf.path.name}: the MP4 does not hold everything the MKV did ({err})"
                  for vf, err in unverified]
        lines += [f"NOT RECYCLED {vf.path.name}: {err}" for vf, err in deletion_failures]
        lines += [f"SKIPPED {vf.path.name}: {vf.path.with_suffix('.mp4').name} already exists" for vf in skipped_existing]
        lines += [
            f"REMUXED {vf.path.name} -> {out.name}" + ("; the original is in the Recycle Bin" if id(vf) in removed_ids else "")
            for vf, out in succeeded if not any(vf is u for u, _e in unverified)
        ]
        self._remux_summary(summary, lines, warn=bool(failed or unverified or deletion_failures))

    def _confirm_remux(self, jobs: list[tuple[Path, Path]]) -> Optional[bool]:
        """The ONE question before a remux batch, with the Recycle Bin choice as a checkbox (off by default):
        a result that kept every track (verify_remux) then sends its original MKV to the Recycle Bin; one that
        lost something always keeps it. Returns None on Cancel, else whether to recycle."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Remux to MP4")
        box.setText(
            f"Remux {len(jobs)} MKV file(s) to MP4? A stream copy: nothing is re-encoded, and each MP4 is checked "
            "for every audio and subtitle track afterwards. The MP4s are added to the list."
        )
        shown = [f"{src.name} -> {out.name}" for src, out in jobs[:200]]
        more = f"\n... and {len(jobs) - len(shown)} more" if len(jobs) > len(shown) else ""
        box.setDetailedText("\n".join(shown) + more)
        check = QCheckBox("Move each original MKV to the Recycle Bin when its MP4 kept every track")
        check.setChecked(False)
        box.setCheckBox(check)
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Yes)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return None
        return check.isChecked()

    def _remux_summary(self, summary: str, lines: list[str], warn: bool) -> None:
        """The one end-of-run box of Media > Remux to MP4, the details under Show Details."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning if warn else QMessageBox.Icon.Information)
        box.setWindowTitle("Remux to MP4")
        box.setText(summary + ".")
        if lines:
            box.setDetailedText("\n".join(lines))
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.exec()

    def _load_results(self, paths: list[Path], label: str) -> list[VideoFile]:
        """Reads freshly written files (ffprobe + tags, VideoFile.load) under a progress dialog -- a batch of
        conversions used to be re-read one by one on the GUI thread with nothing on screen. Not cancellable:
        the files exist either way, and stopping halfway would leave some of them out of the list."""
        loaded: list[VideoFile] = []

        def step(path: Path, _index: int) -> None:
            vf = VideoFile(path=Path(path))
            vf.load()
            loaded.append(vf)

        run_with_progress(
            self, paths, step, label, threshold=1, cancellable=False,
            label_for=lambda p: f"Reading {Path(p).name}",
        )
        return loaded

    def _run_transcode_jobs(
        self, jobs: list[tuple[Path, Path]], settings, title: str,
        remux: bool = False, mkv: bool = False,
    ) -> dict[int, tuple[bool, str]]:
        """Runs `jobs` (_run_transcode_batch) on a worker thread under the shared
        counted progress dialog (redactor_common's run_work_with_progress) and
        returns {job index: (ok, message)} -- an index missing from the result
        never started because the batch was cancelled first. Shared by
        Convert to MP4 and Remux.

        The dialog's Cancel sets progress.cancelled, a threading.Event that is
        handed to ffmpeg as its cancel_event, so a running encode is killed
        (and its partial output removed) mid-file, not only between files."""
        verb = "Remuxing" if remux or mkv else "Converting"

        def work(progress) -> dict[int, tuple[bool, str]]:
            def on_started(i: int, name: str) -> None:
                progress.set_label(f"{verb} {name}... ({i + 1}/{len(jobs)})")
                progress.set_value(i)

            results = _run_transcode_batch(
                jobs, settings, progress.cancelled, remux=remux, mkv=mkv, on_started=on_started,
            )
            progress.set_value(len(jobs))
            return results

        results, _cancelled = run_work_with_progress(self, len(jobs), "Starting...", work, title=title)
        return results

    def _on_convert_to_mp4(self) -> None:
        """Re-encode selected files to H.264/AAC MP4 (batch-capable),
        using the CRF/audio-bitrate/thread defaults from Tools > Preferences >
        Transcode (core/transcode_settings.py). Unlike Remux (-c copy, lossless,
        MKV-only), this applies to any selected file and is a genuine
        re-encode -- slower, and a generation/quality loss versus the
        source, so unlike _on_remux_selected there is deliberately NO
        "delete the original?" prompt here: encouraging a user to throw
        away their only lossless copy right after a lossy conversion is
        the wrong default, even though remux's equivalent prompt is safe
        (a remux loses nothing).

        Runs off the GUI thread via _run_transcode_jobs + the shared progress dialog
        with a working Cancel button -- a real encode takes real time,
        unlike remux's near-instant stream copy.
        """
        selected = self._selected_video_files()
        if not selected:
            QMessageBox.information(
                self, "Nothing to Convert",
                "Select at least one video file to convert.",
            )
            return

        settings = get_transcode_settings()
        jobs: list[tuple[Path, Path]] = []
        skipped_existing: list[VideoFile] = []
        claimed: set[str] = set()
        for vf in selected:
            if vf.path.suffix.lower() == ".mp4":
                # Converting an MP4 in place would mean reading and
                # writing the same file at once -- name the output
                # distinctly instead, same "never silently overwrite
                # the input" reasoning as remux's existing-file check
                # below (which only ever applies to non-MP4 sources).
                output_path = vf.path.with_name(f"{vf.path.stem}_h264.mp4")
            else:
                output_path = vf.path.with_suffix(".mp4")
            if not _claim_output(output_path, claimed):
                skipped_existing.append(vf)
                continue
            jobs.append((vf.path, output_path))

        if not jobs:
            QMessageBox.information(
                self, "Nothing to Convert",
                "Every selected file's converted output already exists.",
            )
            return

        thread_note = f", {settings.threads} threads" if settings.threads else ""
        confirm = QMessageBox.question(
            self, "Convert to MP4 (H.264)",
            f"Re-encode {len(jobs)} file(s) to H.264/AAC MP4 "
            f"(CRF {settings.crf}, audio {settings.audio_bitrate}{thread_note})?\n\n"
            "This re-encodes video -- slower than Remux, and a quality/"
            "generation loss versus the source. Originals are never "
            "deleted automatically. Adjust these defaults in "
            "Tools > Preferences > Transcode.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        results = self._run_transcode_jobs(jobs, settings, "Converting to MP4")

        outputs: list[Path] = []
        failed: list[tuple[Path, str]] = []
        cancelled_count = 0
        for i, (input_path, output_path) in enumerate(jobs):
            result = results.get(i)
            if result is None:
                cancelled_count += 1  # never started -- batch was cancelled first
                continue
            ok, message = result
            if ok:
                outputs.append(output_path)
            elif message == "Cancelled":
                cancelled_count += 1
            else:
                failed.append((output_path, message))
        succeeded = len(outputs)

        self.video_files.extend(self._load_results(outputs, "Reading the converted files..."))
        self._refresh_table_rows()

        parts = [f"Converted {succeeded} file(s)"]
        if failed:
            parts.append(f"{len(failed)} failed")
        if cancelled_count:
            parts.append(f"{cancelled_count} cancelled")
        if skipped_existing:
            parts.append(f"{len(skipped_existing)} skipped (output already exists)")
        summary = ", ".join(parts)
        self.status_bar.showMessage(summary)

        # ONE end-of-run summary: every per-file warning and error is in its detailed text, never a box per file.
        lines = [f"FAILED {path.name}: {err.strip() or 'ffmpeg conversion failed'}" for path, err in failed]
        lines += [f"SKIPPED {vf.path.name}: the converted output already exists" for vf in skipped_existing]
        lines += [f"CONVERTED {path.name}" for path in outputs]
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning if failed else QMessageBox.Icon.Information)
        box.setWindowTitle("Convert to MP4 (H.264)")
        box.setText(summary + ".")
        box.setDetailedText("\n".join(lines))
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.exec()

    # --- Import & Convert / Convert with the rules -----------------------------

    def _on_import_and_convert(self) -> None:
        """File > Import and Convert...: brings a non-MP4/MKV video file (AVI/MOV/WMV/FLV/WebM/MPG/...)
        into the library through the conversion rules (core/conversion_plan.py: stream copy into MP4 or
        MKV, re-encode only what no container can hold, external .srt files muxed in), the same engine as
        the Redact recipe's convert step and Media > Convert to MP4/MKV (Rules). The H.264 re-encode is
        only Media > Convert to MP4 (H.264), chosen on purpose. Additive to the loaded list."""
        self._convert_with_rules(self._pick_videos_to_convert("Import and Convert", IMPORTABLE_EXTENSIONS))

    def _pick_videos_to_convert(self, title: str, extensions) -> list[str]:
        extensions_filter = " ".join(f"*{ext}" for ext in sorted(extensions))
        last_folder = get_setting("general", "last_folder", "")
        paths, _ = QFileDialog.getOpenFileNames(self, title, last_folder, f"Video Files ({extensions_filter})")
        return list(paths)

    def _import_paths(self, paths: list[str]) -> None:
        """Converts `paths` (from the picker, or found by Open Folder) with the rules engine and adds the
        results to the list."""
        self._convert_with_rules(paths)

    def _on_convert_with_rules(self) -> None:
        """Media > Convert to MP4/MKV (Rules)...: the library conversion rules on the selected files, or,
        with nothing selected, on files picked from disk (any importable format, loaded or not). Each run
        asks "Dry run first" (default on) instead of relying on the persistent Library safety setting."""
        selected = self._selected_video_files()
        if selected:
            paths = [str(vf.path) for vf in selected]
        else:
            paths = self._pick_videos_to_convert(
                "Convert to MP4/MKV (Rules)", set(IMPORTABLE_EXTENSIONS) | set(SUPPORTED_EXTENSIONS),
            )
        self._convert_with_rules(paths)

    @staticmethod
    def _rules_text() -> str:
        """What the conversion rules do and where the original goes, for the confirm / offer dialogs."""
        safety = library_safety.get_safety_settings()
        return (
            "Legacy containers become MP4 or MKV by stream copy; only video no container can hold is re-encoded; "
            "series episodes over the bitrate limits are re-encoded (720p); .srt files beside a video are muxed in. "
            "Every result is verified before the original is replaced.\n\n"
            + original_fate_text(safety, True).split(". ", 1)[-1]
            + "\n\nLimits: Tools > Preferences > Conversion. Safety: Tools > Preferences > Library safety."
        )

    @staticmethod
    def _dry_run_checkbox() -> QCheckBox:
        """The per-run "Dry run first" choice, on by default."""
        box = QCheckBox("Dry run first: list what would happen, change nothing")
        box.setChecked(True)
        return box

    def _convert_with_rules(self, paths: list[str], dry_run: Optional[bool] = None) -> None:
        """The one conversion flow: confirm (with the per-run "Dry run first" checkbox), run
        conversion_run.process_file through a SafeRun for each path under a cancellable progress dialog
        (Cancel kills ffmpeg), then report every file -- a dry run as explicit "DRY RUN: would ..." lines --
        and refresh / add the affected rows. A caller that already asked (the Open Folder offer, with its own
        dry-run checkbox) passes `dry_run` and no second confirm is shown."""
        paths = [p for p in dict.fromkeys(os.path.abspath(p) for p in paths)]
        if not paths:
            return
        safety = library_safety.get_safety_settings()
        conv = conversion_plan.get_conversion_settings()
        if dry_run is None:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Question)
            box.setWindowTitle("Convert to MP4/MKV (Rules)")
            box.setText(f"Apply the conversion rules to {len(paths)} file(s)?\n\n" + self._rules_text())
            box.setDetailedText("\n".join(paths[:200]) + (f"\n... and {len(paths) - 200} more" if len(paths) > 200 else ""))
            dry_box = self._dry_run_checkbox()
            box.setCheckBox(dry_box)
            box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
            if box.exec() != QMessageBox.StandardButton.Yes:
                return
            dry_run = dry_box.isChecked()

        loaded_by_path = {os.path.normcase(os.path.abspath(str(vf.path))): vf for vf in self.video_files}
        cancel_event = threading.Event()
        self._active_cancel = cancel_event
        lines: list[str] = []
        converted: list[tuple[str, str]] = []  # (source, final)
        cancelled = False

        def one(path: str, _index: int) -> None:
            nonlocal cancelled
            if cancel_event.is_set():
                cancelled = True
                return
            vf = loaded_by_path.get(os.path.normcase(path))
            if vf is not None and vf.dirty and not vf.stamp_only_dirty:
                lines.append(f"{os.path.basename(path)}: skipped, it has unsaved edits")
                return
            # A series episode: tagged TV, or untagged (or not loaded) with an episode-like name.
            is_series = conversion_is_series(
                vf.metadata.content_type if vf is not None else None, os.path.splitext(os.path.basename(path))[0],
            )
            run = make_safe_run(get_setting, safety, dry_run=dry_run)
            # The app's tags travel with the file: a loaded row hands over its own (saved) tags, any other
            # file has them read from disk just before it is replaced (conversion_run.process_file).
            tags = None
            if vf is not None:
                tags = copy.deepcopy(vf.metadata)
                tags.scan_stamp = ""  # the stamp describes the old file's bytes
            try:
                outcome = call_in_background(
                    conversion_run.process_file, run, path, conv, is_series=is_series, cancel_event=cancel_event,
                    tags=tags,
                )
            except library_safety.SafetyError as exc:
                lines.append(f"{os.path.basename(path)}: not converted, the file is untouched: {exc}")
                return
            except BackgroundCancelled:
                cancelled = True
                return
            notes = ("; " + "; ".join(outcome.notes)) if outcome.notes else ""
            if outcome.message == "Cancelled" or any(a.detail.endswith("Cancelled)") for a in run.actions):
                cancelled = True
                lines.append(f"{os.path.basename(path)}: cancelled, the file is untouched")
                return
            if not outcome.converted:
                skipped = [a for a in run.actions if a.verb == "skip"]
                lines.append(f"{os.path.basename(path)}: {skipped[0].detail if skipped else outcome.message}{notes}")
                return
            if dry_run:
                lines.append(f"{os.path.basename(path)}: {dry_run_line(run.actions)}{notes}")
                return
            converted.append((path, outcome.final_path))
            done = "; ".join(f"{a.verb}: {os.path.basename(a.path)} -> {os.path.basename(a.detail)}" for a in run.actions if a.done)
            lines.append(f"{os.path.basename(path)}: converted to {os.path.basename(outcome.final_path)} ({done}){notes}")

        try:
            finished = run_with_progress(
                self, paths, one, "Converting with the rules...", threshold=1, cancellable=True,
                label_for=lambda p: f"Converting {os.path.basename(p)}", on_cancel=cancel_event.set,
            )
        finally:
            self._active_cancel = None
        cancelled = cancelled or not finished

        # Loaded rows follow their converted file; converted files that were not loaded are added. Each is
        # re-read (ffprobe + tags) under a progress dialog, not on a frozen GUI thread.
        new_files: list[VideoFile] = []

        def reload(item: tuple[str, str], _index: int) -> None:
            source, final = item
            vf = loaded_by_path.get(os.path.normcase(source))
            if vf is None:
                vf = VideoFile(path=Path(final))
                new_files.append(vf)
            else:
                vf.path = Path(final)
                vf.dirty = False
            vf.load()

        run_with_progress(
            self, converted, reload, "Reading the converted files...", threshold=1, cancellable=False,
            label_for=lambda item: f"Reading {os.path.basename(item[1])}",
        )
        if new_files:
            self.video_files.extend(new_files)
        if converted:
            set_setting("general", "last_folder", str(Path(paths[0]).parent))
            self._clear_undo()
        self._after_batch_edit(
            ("Dry run: nothing changed" if dry_run else f"Converted {len(converted)} file(s)") + (", cancelled" if cancelled else "")
        )
        title = "Dry run" if dry_run else "Convert to MP4/MKV (Rules)"
        summary = (
            f"DRY RUN of {len(paths)} file(s): nothing was changed. Each line says what a real run would do."
            if dry_run else f"{len(converted)} of {len(paths)} file(s) converted."
        ) + (" Cancelled before the end." if cancelled else "")
        report = QMessageBox(self)
        report.setIcon(QMessageBox.Icon.Information)
        report.setWindowTitle(title)
        report.setText(summary)
        report.setDetailedText("\n".join(lines))
        report.setStandardButtons(QMessageBox.StandardButton.Ok)
        report.exec()

    # --- Subtitles -------------------------------------------------------

    def _on_import_subtitles(self) -> None:
        """Import subtitles from OpenSubtitles for exactly one selected
        file (renamed from "Fetch Subtitles" per explicit request).
        Single-file scope, same reasoning as TMDB import: hash-matching
        is inherently per-file (each file has its own fingerprint), and
        a title-search fallback needs the user to actually read and
        confirm the sync-risk warning per result, which doesn't batch
        sensibly.
        """
        selected = self._selected_video_files()
        if len(selected) != 1:
            QMessageBox.information(
                self, "Select One File",
                "Subtitle search works on one file at a time. Select exactly one row.",
            )
            return

        vf = selected[0]
        if vf.load_error:
            QMessageBox.warning(
                self, "Cannot Import Subtitles",
                f"{vf.path.name} failed to load ({vf.load_error}) -- fix that first.",
            )
            return

        dialog = SubtitleSearchDialog(str(vf.path), language="en", parent=self, video=vf)
        if not dialog.exec():
            return
        if dialog.source == SOURCE_LRCLIB:
            self._save_lyrics_subtitle(vf, dialog)
            return
        if dialog.selected_candidate is None:
            return
        candidate = dialog.selected_candidate

        try:
            subtitle_text = run_lookup(self, download_subtitle_text, candidate.file_id)
        except OpenSubtitlesError as e:
            QMessageBox.warning(self, "Subtitle Download Failed", str(e))
            return

        status, detail = self._save_sidecar(
            lambda overwrite: vf.save_subtitle_sidecar(subtitle_text, language=candidate.language, overwrite=overwrite),
            {},
        )
        if status == "error":
            QMessageBox.warning(self, "Subtitle Not Saved", f"Couldn't write the subtitle file: {detail}")
            return
        if status == "kept":
            self.status_bar.showMessage("Kept the existing subtitle file")
            return

        sync_note = "exact match" if candidate.hash_matched else "title match -- sync not guaranteed"
        self.status_bar.showMessage(f"Saved subtitle to {detail.name} ({sync_note})")

    def _one_loaded_video(self, what: str) -> Optional[VideoFile]:
        files = self._selected_video_files()
        if len(files) != 1:
            self.status_bar.showMessage(f"{what} needs exactly one file selected")
            return None
        if files[0].load_error:
            self.status_bar.showMessage(f"{files[0].path.name} failed to load: fix that first")
            return None
        return files[0]

    def _on_translate_subtitles(self, preselect: str = "") -> None:
        """Media > Translate Subtitles to English...: the side-by-side translation dialog for the one selected
        video (never translates or writes anything until its buttons are pressed)."""
        vf = self._one_loaded_video("Translate Subtitles")
        if vf is None:
            return
        dialog = SubtitleTranslationDialog(vf, MODE_TRANSLATE, parent=self, preselect=preselect or "")
        dialog.exec()
        if dialog.written_path is not None:
            self.status_bar.showMessage(f"Saved {dialog.written_path.name} (machine translation)")

    def _on_compare_subtitles(self) -> None:
        """Media > Compare Subtitles...: any two subtitle tracks of the selected video side by side, read only."""
        vf = self._one_loaded_video("Compare Subtitles")
        if vf is not None:
            SubtitleTranslationDialog(vf, MODE_COMPARE, parent=self).exec()

    def _save_lyrics_subtitle(self, vf: VideoFile, dialog: SubtitleSearchDialog) -> None:
        """Writes the SRT the Lyrics (LRCLIB) page built (offset already applied) as the usual sidecar
        <stem>.<lang>.srt; an existing file is only replaced when the user says so."""
        srt, language, match = dialog.selected_srt, dialog.selected_language, dialog.selected_match
        status, detail = self._save_sidecar(
            lambda overwrite: vf.save_subtitle_sidecar(srt, language=language, overwrite=overwrite), {}
        )
        if status == "error":
            QMessageBox.warning(self, "Subtitle Not Saved", f"Couldn't write the subtitle file: {detail}")
            return
        if status == "kept":
            self.status_bar.showMessage("Kept the existing subtitle file")
            return
        self.status_bar.showMessage(f"Saved lyrics subtitle to {detail.name} (LRCLIB, {match.score.label})")
        if getattr(dialog, "translate_requested", False):
            self._on_translate_subtitles(preselect=str(detail))

    # --- Filename patterns -------------------------------------------------

    def _batch_targets(self, verb: str) -> list[VideoFile]:
        """The selection, or every loaded file if nothing is selected --
        the family's convention for whole-batch operations. Files that
        failed to load are excluded; an empty list means "nothing to do"
        and the user has already been told why."""
        selected = self._selected_video_files()
        targets = [vf for vf in (selected or self._visible_video_files()) if not vf.load_error]
        if not targets:
            QMessageBox.information(self, "No Files", f"Open some files first (File > Open Files or Open Folder), or select the ones to {verb}.")
        return targets

    def _on_number_episodes(self) -> None:
        """Metadata > Number Episodes...: the quick numbering of the selected
        files (the same thing the row right-click menu offers)."""
        files = self._selected_video_files()
        if not files:
            self.status_bar.showMessage("No files selected")
            return
        self._quick_number_episodes(files)

    def _after_batch_edit(self, message: str) -> None:
        self._refresh_table_rows()
        if self._selected_video_files():
            self._on_selection_changed()
        self.status_bar.showMessage(message)

    def _on_rename_by_pattern(self) -> None:
        """Rename (or export copies of) files by a %field% pattern --
        redactor_common's shared RenamePatternDialog since 2026-09-23,
        which added export-to-folder mode, a clickable placeholder list,
        optional [...] groups and automatic " (2)" de-duplication instead
        of refusing the whole batch on a name collision. Acts on disk
        immediately; not undoable, same as the other apps."""
        targets = self._batch_targets("rename")
        if not targets:
            return
        dialog = RenamePatternDialog(
            targets, FILENAME_PLACEHOLDERS,
            lambda vf: placeholder_values(vf.metadata),
            lambda vf: str(vf.path),
            pattern_history=load_pattern_history(),
            default_pattern=DEFAULT_RENAME_PATTERN,
            item_noun="file",
            zero_pad_field="episode_number",
            zero_pad_label="Zero-pad episode # to:",
            ascii_only=get_setting("rename", "ascii_only", "0") == "1",
            on_ascii_only_changed=lambda on: set_setting("rename", "ascii_only", "1" if on else "0"),
            zero_pad_initial=(
                get_setting("rename", "zero_pad", "0") == "1",
                int(get_setting("rename", "zero_pad_width", "2") or 2),
            ),
            on_zero_pad_changed=_remember_zero_pad,
            library_root=get_setting("rename", "library_root", ""),
            on_library_root_changed=lambda folder: set_setting("rename", "library_root", folder),
            parent=self,
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return

        save_pattern_to_history(dialog.pattern_edit.text())
        if dialog.is_move_mode():
            set_setting("rename", "move_pattern", dialog.pattern_edit.text())
            self._move_into_folders(dialog.planned_moves())
            return
        export_mode = dialog.is_export_mode()
        done = 0
        errors: list[str] = []
        renamed: list[tuple[str, str]] = []
        planned = [
            (vf, old_path, new_path) for vf, old_path, new_path in dialog.planned_renames()
            if os.path.normcase(os.path.abspath(old_path)) != os.path.normcase(os.path.abspath(new_path))
        ]

        def one(item, _index: int) -> None:
            nonlocal done
            vf, old_path, new_path = item
            try:
                if export_mode:
                    _copy_no_clobber(old_path, new_path)
                else:
                    rename_no_clobber(old_path, new_path)
                    vf.path = Path(new_path)
                    renamed.append((str(old_path), str(new_path)))
                done += 1
            except OSError as exc:
                errors.append(f"{os.path.basename(old_path)}: {exc}")

        # Copies (Export) of large videos take real time, and renames on a network share are not instant.
        finished = run_with_progress(
            self, planned, one, "Exporting files..." if export_mode else "Renaming files...", threshold=3,
            label_for=lambda item: f"{'Copying' if export_mode else 'Renaming'} {os.path.basename(item[1])}",
        )
        _rename_log().record("Rename by Pattern", renamed)

        self._refresh_table_rows()
        verb = "Exported" if export_mode else "Renamed"
        cancel_note = f", cancelled -- {len(planned) - done - len(errors)} not done" if not finished else ""
        self.status_bar.showMessage(f"{verb} {done} file(s){cancel_note}")
        if errors:
            QMessageBox.warning(self, "Some files failed", _error_details(errors))

    def _move_into_folders(self, planned) -> None:
        """The Rename dialog's "Move into folders" mode: the shared runner
        moves the files (progress, Cancel, per-file errors, the empty-folder
        tidy-up question) and records them -- with the folders it created --
        as one batch in the rename log, so File > Undo Last Rename puts them
        back. Each video's poster/subtitle sidecar files (see core/sidecars)
        are planned right after it, so they travel with it. Like a rename it
        isn't on the Undo stack; the rows just take on their new paths."""
        videos = len(planned)
        summary = run_planned_moves(
            self, with_sidecars(planned), copy=False, rename_log=_rename_log(), label="Move into Folders",
            trash=move_to_trash,
        )
        moved = 0
        for item, _old_path, new_path in summary.done:
            if item is not None:  # None: a sidecar file
                item.path = Path(new_path)
                moved += 1
        self._refresh_table_rows()
        sidecars = len(summary.done) - moved
        note = f" (+{sidecars} sidecar file(s))" if sidecars else ""
        self.status_bar.showMessage(f"Moved {moved} of {videos} file(s) into folders{note}")

    def _on_import_metadata_from_filename(self) -> None:
        """Extract metadata from filenames (or, with a "/" pattern, the
        folder path) into staged (unsaved) fields, via redactor_common's
        shared ParseFilenameDialog. Season/Episode #/Rating only match
        digits (so "S01E03" or a "Season 02" folder parses cleanly) and
        lose their zero-padding."""
        targets = self._batch_targets("parse")
        if not targets:
            return
        dialog = ParseFilenameDialog(
            targets, FILENAME_PLACEHOLDERS, lambda vf: str(vf.path),
            pattern_history=load_pattern_history(),
            default_pattern=DEFAULT_RENAME_PATTERN,
            valid_field_keys=set(VALID_FIELD_KEYS),
            numeric_fields=set(PARSE_NUMERIC_FIELDS),
            strip_leading_zeros_fields=set(PARSE_STRIP_ZEROS_FIELDS),
            title="Import Metadata from Filename",
            item_noun="file",
            # A pattern with "/" reads the folders too; same root as Move into folders.
            library_root=get_setting("rename", "library_root", ""),
            on_library_root_changed=lambda folder: set_setting("rename", "library_root", folder),
            parent=self,
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return

        save_pattern_to_history(dialog.pattern_edit.text())
        changes = dialog.accepted_changes()
        if not changes:
            self.status_bar.showMessage("Import from Filename: no file had a change to apply")
            return
        self._push_undo("Import from Filename", [targets[i] for i in changes])
        for index, fields in changes.items():
            vf = targets[index]
            for field_name, value in fields.items():
                if value:
                    _set_field_from_text(vf, field_name, value)
            vf.dirty = True
        self._after_batch_edit(
            f"Imported metadata from filename for {len(changes)} file(s) -- not yet saved to disk"
        )

    # --- Batch text operations (Edit menu) ------------------------

    def _text_field_choices(self) -> list[tuple[str, str]]:
        return [(field, FIELD_LABELS.get(field, field)) for field in TEXT_FIELDS]

    def _on_case_conversion(self) -> None:
        """Batch case conversion for a chosen text field, staged
        (unsaved), via redactor_common's shared dialog -- per-row Apply
        checkboxes; title case keeps this app's "Star Wars: A New Hope"
        clause rule, now part of the shared implementation."""
        targets = self._batch_targets("convert")
        if not targets:
            return
        dialog = CaseConversionDialog(
            targets, self._text_field_choices(), _field_text,
            lambda vf: vf.path.name, item_noun="file", parent=self,
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        self._apply_single_field_changes("Case Conversion", targets, dialog.result_field_key(),
                                         dialog.accepted_changes(), "Converted case for")

    def _on_search_replace(self) -> None:
        """Batch find-and-replace within a chosen text field (or the
        filename itself), via redactor_common's shared dialog -- adds
        regex support and per-row Apply checkboxes. A filename change
        renames on disk immediately; field changes are staged."""
        targets = self._batch_targets("search")
        if not targets:
            return

        def get_value(vf: VideoFile, field_key: str) -> str:
            if field_key == FILENAME_FIELD_KEY:
                return vf.path.stem
            return _field_text(vf, field_key)

        dialog = SearchReplaceDialog(
            targets, self._text_field_choices(), get_value,
            lambda vf: vf.path.name, include_filename=True, item_noun="file", parent=self,
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        field_key = dialog.result_field_key()
        changes = dialog.accepted_changes()
        if field_key != FILENAME_FIELD_KEY:
            self._apply_single_field_changes("Search & Replace", targets, field_key, changes, "Replaced text in")
            return

        errors: list[str] = []
        renamed = 0
        logged: list[tuple[str, str]] = []

        def one(item: tuple[int, str], _index: int) -> None:
            nonlocal renamed
            index, new_stem = item
            vf = targets[index]
            new_path = vf.path.with_name(new_stem + vf.path.suffix)
            try:
                if new_path.exists() and new_path != vf.path:
                    raise FileExistsError(f"{new_path.name} already exists")
                old_path = vf.path
                rename_no_clobber(str(vf.path), str(new_path))
                vf.path = new_path
                logged.append((str(old_path), str(new_path)))
                renamed += 1
            except OSError as exc:
                errors.append(f"{vf.path.name}: {exc}")

        run_with_progress(
            self, list(changes.items()), one, "Renaming files...", threshold=3,
            label_for=lambda item: f"Renaming {targets[item[0]].path.name}",
        )
        _rename_log().record("Search/Replace (filename)", logged)
        self._after_batch_edit(f"Renamed {renamed} file(s)")
        if errors:
            QMessageBox.warning(self, "Some files failed to rename", _error_details(errors))

    def _apply_single_field_changes(
        self, label: str, targets: list[VideoFile], field_key: str,
        changes: dict[int, str], verb: str,
    ) -> None:
        """Shared tail of the one-field batch dialogs: stage each accepted
        new value (undoably), then refresh."""
        if not changes:
            self.status_bar.showMessage(f"{label}: no file had a change to apply")
            return
        self._push_undo(label, [targets[i] for i in changes])
        applied = 0
        for index, new_value in changes.items():
            vf = targets[index]
            if _set_field_from_text(vf, field_key, new_value):
                vf.dirty = True
                applied += 1
        self._after_batch_edit(f"{verb} {applied} file(s) -- not yet saved to disk")

    def _quick_number_episodes(self, files: list[VideoFile]) -> None:
        """The table right-click's quick version of Auto-Numbering:
        just prompts for a starting Episode # (no field picker, no
        step, no preview) and numbers the given files +1 per row from
        there, in their current table order. For anything beyond the
        plain "start here, count up by one" case on Episode #
        specifically -- a different field, a different step, or a look
        at what's changing before it does -- use
        Edit > Auto-Number... instead. Undoable (Ctrl+Z).
        """
        values = prompt_and_generate_series_numbers(self, len(files), field_label="Starting Episode #")
        if values is None:
            return
        self._push_undo("Number Episodes", files)
        for vf, new_value in zip(files, values):
            try:
                vf.metadata.episode_number = int(float(new_value))
            except ValueError:
                continue  # a genuinely unparseable value -- leave this file untouched
            vf.dirty = True
        self._refresh_table_rows()
        if self._selected_video_files():
            self._on_selection_changed()

    def _on_auto_numbering(self) -> None:
        """Batch sequential numbers into a chosen field, staged (unsaved),
        in the files' current table order, via redactor_common's shared
        dialog (which started life as this project's own)."""
        targets = self._batch_targets("number")
        if not targets:
            return
        fields = [
            (field, FIELD_LABELS.get(field, field), field in NUMERIC_FIELDS)
            for field in NUMERIC_FIELDS + TEXT_FIELDS
        ]
        dialog = AutoNumberingDialog(
            targets, fields, _field_text, lambda vf: vf.path.name, item_noun="file",
            padding=int(get_setting("auto_numbering", "padding", "2") or 2),
            on_padding_changed=lambda width: set_setting("auto_numbering", "padding", str(width)),
            parent=self,
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        self._apply_single_field_changes("Auto-Numbering", targets, dialog.result_field_key(),
                                         dialog.accepted_changes(), "Auto-numbered")

    # --- Undo / Redo -----------------------------------------------------

    @staticmethod
    def _snapshot(vf: VideoFile) -> tuple:
        return copy.deepcopy(vf.metadata), vf.dirty

    @staticmethod
    def _restore(vf: VideoFile, snapshot: tuple) -> None:
        # A scan isn't an undoable edit: undo keeps the file's current
        # stamp (and stays dirty while that stamp is still unsaved).
        stamp_changed = vf.metadata.scan_stamp != snapshot[0].scan_stamp
        scan_stamp = vf.metadata.scan_stamp
        vf.metadata, vf.dirty = copy.deepcopy(snapshot[0]), snapshot[1]
        vf.metadata.scan_stamp = scan_stamp
        vf.dirty = vf.dirty or stamp_changed

    def _push_undo(self, label: str, files: list[VideoFile]) -> None:
        """Call BEFORE mutating `files`, to capture their prior state."""
        if not files:
            return
        self.undo_manager.push(label, files, self._snapshot)
        self._update_undo_actions()

    def _clear_undo(self) -> None:
        self.undo_manager.clear()
        self._update_undo_actions()

    def _update_undo_actions(self) -> None:
        undo_label = self.undo_manager.peek_label()
        redo_label = self.undo_manager.peek_redo_label()
        self.undo_action.setEnabled(self.undo_manager.can_undo())
        self.undo_action.setText(f"&Undo {undo_label}" if undo_label else "&Undo")
        self.redo_action.setEnabled(self.undo_manager.can_redo())
        self.redo_action.setText(f"&Redo {redo_label}" if redo_label else "&Redo")

    def undo_last_action(self) -> None:
        # snapshot_fn too, so the state being overwritten goes onto the
        # redo stack -- see redactor_common.core.undo.
        if self.undo_manager.undo(self._restore, self._snapshot):
            self._after_batch_edit("Undone")
        self._update_undo_actions()

    def redo_last_action(self) -> None:
        if self.undo_manager.redo(self._restore, self._snapshot):
            self._after_batch_edit("Redone")
        self._update_undo_actions()

    # --- Help ------------------------------------------------------------

    def _on_show_about(self) -> None:
        """Version/about dialog -- now the shared redactor_common
        AboutDialog (Markdown-rendering, matches the epub and mp3
        tools) rather than this project's own plain QMessageBox.about()
        popup, which never rendered ABOUT.md at all despite the file
        existing on disk.
        """
        AboutDialog(
            app_name=APP_NAME,
            app_version=APP_VERSION,
            release_label=RELEASE_LABEL,
            icon_path=str(ICON_PATH),
            about_path=str(ABOUT_PATH),
            component_versions={"redactor_common": REDACTOR_COMMON_VERSION},
            repo_url=APP_REPO_URL,
            component_repo_urls={"redactor_common": REDACTOR_COMMON_REPO_URL},
            parent=self,
        ).exec()

    def _on_show_changelog(self) -> None:
        """Full in-app CHANGELOG.md viewer -- now the shared
        redactor_common ChangelogDialog rather than this project's own
        local MarkdownViewerDialog, which did the same thing with a
        second, separately-maintained implementation.
        """
        ChangelogDialog(str(CHANGELOG_PATH), parent=self).exec()

    def _on_show_credits(self) -> None:
        AppCreditsDialog(str(CREDITS_PATH), parent=self).exec()
