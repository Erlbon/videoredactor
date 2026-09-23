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
from typing import Optional
import copy
import json
import os
import shutil
import threading

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QSplitter,
    QTableWidget, QTableWidgetItem, QComboBox, QLabel, QFileDialog,
    QStatusBar, QMessageBox, QToolBar,
)
from PyQt6.QtCore import Qt, QThread, QEventLoop, QSize, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QKeySequence, QIcon

from core.video_file import VideoFile, discover_video_files, has_subfolders
from core.video_metadata import ContentType, EDITABLE_FIELDS, NUMERIC_FIELDS, TEXT_FIELDS
from core.filename_pattern import (
    DEFAULT_RENAME_PATTERN, PARSE_NUMERIC_FIELDS, PARSE_STRIP_ZEROS_FIELDS, VALID_FIELD_KEYS,
    field_text, load_pattern_history, placeholder_values, save_pattern_to_history,
)
from core.tmdb_client import (
    get_movie_details, get_tv_show_details, get_tv_episode_details,
    download_poster, TMDBError,
)
from core.tvdb_client import get_series_details, get_episode_details, download_image, TVDBError
from core.release_name_parser import parse_release_name
from core.ffmpeg_backend import IMPORTABLE_EXTENSIONS, remux_to_mp4, transcode_to_mp4
from core.transcode_settings import get_transcode_settings
from core.opensubtitles_client import download_subtitle_text, OpenSubtitlesError
from core.table_settings import PROTECTED_COLUMNS, merge_column_order, is_column_visible, sanitize_hidden_fields
from core.format_helpers import format_duration, format_file_size
from core.config import get_setting, set_setting
from redactor_common.gui.action_factory import make_action
from redactor_common.gui.menu_builder import MenuAction, Separator, build_menu_bar
from redactor_common.gui.async_preview import AsyncPreviewLoader
from redactor_common.gui.auto_numbering_dialog import AutoNumberingDialog
from redactor_common.gui.case_conversion_dialog import CaseConversionDialog
from redactor_common.gui.parse_filename_dialog import ParseFilenameDialog
from redactor_common.gui.rename_pattern_dialog import RenamePatternDialog
from redactor_common.gui.search_replace_dialog import FILENAME_FIELD_KEY, SearchReplaceDialog
from redactor_common.gui.progress import ProgressReporter, run_with_progress
from redactor_common.gui.colors import DIRTY_COLOR, ERROR_COLOR, HIGHLIGHT_TEXT_COLOR, TABLE_SELECTION_STYLESHEET
from redactor_common.gui.context_menu import show_table_context_menu
from redactor_common.gui.quick_series_number import prompt_and_generate_series_numbers
from redactor_common.gui.column_menu import show_column_header_context_menu
from redactor_common.gui.column_settings_dialog import ColumnSettingsDialog
from redactor_common.gui.collapsible_splitter import SplitterPaneCollapser
from redactor_common.gui.zoom_toolbar import TableZoomController
from redactor_common.gui.about_dialog import AboutDialog, ChangelogDialog, CreditsDialog
from redactor_common.gui.rename_single_file import rename_single_file as prompt_rename_single_file
from redactor_common.gui.sortable_table import NumericTableWidgetItem, suspend_sorting
from redactor_common.gui import standard_shortcuts as shortcuts
from redactor_common.core.error_summary import summarize_errors
from redactor_common.core.undo import UndoManager
from redactor_common.core.folder_refresh import find_new_files_in_loaded_folders
from redactor_common.core.version import REDACTOR_COMMON_REPO_URL, REDACTOR_COMMON_VERSION
from gui.tag_panel import TagPanel, FIELD_LABELS
from gui.tmdb_search_dialog import TMDBSearchDialog
from gui.tmdb_episode_picker_dialog import TVEpisodePickerDialog
from gui.tvdb_search_dialog import TVDBSearchDialog
from gui.tvdb_episode_picker_dialog import TVDBEpisodePickerDialog
from gui.subtitle_search_dialog import SubtitleSearchDialog
from gui.tool_settings_dialog import ToolSettingsDialog
from gui.vocabulary_editor_dialog import VocabularyEditorDialog
from gui.api_keys_dialog import ApiKeysDialog
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
TECHNICAL_COLUMNS = ["path", "duration_seconds", "resolution", "size_bytes"]
TECHNICAL_LABELS = {
    "path": "Path",
    "duration_seconds": "Duration",
    "resolution": "Resolution",
    "size_bytes": "Size",
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


def _field_text(vf: VideoFile, field_name: str) -> str:
    """A metadata field as the plain string the shared dialogs work on."""
    return field_text(vf.metadata, field_name)


def _set_field_from_text(vf: VideoFile, field_name: str, text: str) -> bool:
    """Stores a string from a shared dialog back into its typed field.
    Returns False (and leaves the field untouched) for a value that
    doesn't fit: a non-number for an int field, or an unknown Content
    Type -- skipping one field beats crashing the whole batch or writing
    a string into an int field."""
    text = (text or "").strip()
    if field_name in NUMERIC_FIELDS:
        if not text:
            setattr(vf.metadata, field_name, None)
            return True
        try:
            setattr(vf.metadata, field_name, int(float(text)))
        except ValueError:
            return False
        return True
    if field_name == "content_type":
        try:
            vf.metadata.content_type = ContentType(text)
        except ValueError:
            return False
        return True
    setattr(vf.metadata, field_name, text)
    return True


def _error_details(lines: list[str]) -> str:
    """A bounded "N file(s): a; b; c, ..." message-box body -- ffmpeg's
    stderr alone can run to hundreds of lines per file, and a failed
    batch of hundreds of files used to produce a message box taller
    than the screen, with its OK button out of reach."""
    return f"{len(lines)} file(s):\n\n{summarize_errors(lines)}"


class _TranscodeWorker(QThread):
    """Runs a batch of transcode_to_mp4() calls off the GUI thread.

    Unlike _on_remux_selected's loop (safe to run straight on the GUI
    thread since -c copy is near-instant), a real H.264/AAC re-encode
    can take minutes per file -- doing that inline would freeze the
    whole window for the entire batch. Emits progress per file rather
    than returning everything at once, so the caller's QProgressDialog
    can update between files; `cancel_event` is checked both between
    files here and (via transcode_to_mp4) mid-file, so Cancel actually
    stops a long encode instead of only skipping ones not yet started.
    """

    file_started = pyqtSignal(int, str)       # index, input filename
    file_finished = pyqtSignal(int, bool, str)  # index, success, stderr/message

    def __init__(self, jobs: list[tuple[Path, Path]], settings, parent=None):
        super().__init__(parent)
        self._jobs = jobs
        self._settings = settings
        self.cancel_event = threading.Event()

    def run(self) -> None:
        for i, (input_path, output_path) in enumerate(self._jobs):
            if self.cancel_event.is_set():
                break
            self.file_started.emit(i, input_path.name)
            ok, message = transcode_to_mp4(
                str(input_path), str(output_path),
                crf=self._settings.crf,
                audio_bitrate=self._settings.audio_bitrate,
                threads=self._settings.threads or None,
                cancel_event=self.cancel_event,
            )
            self.file_finished.emit(i, ok, message)


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
        self._rebuild_table_columns(content_type_filter=None)
        self._check_external_tools_on_startup()
        self._restore_last_folder_on_startup()

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)

        # --- Top bar: content type filter ---
        # (Open Folder / Save moved to the toolbar -- see _build_toolbar --
        # so this row is just the filter now, not a mix of the two.)
        top_bar = QHBoxLayout()
        top_bar.addWidget(QLabel("Filter by Content Type:"))
        self.filter_combo = QComboBox()
        self.filter_combo.addItem("All (no filter)", None)
        for ct in ContentType:
            if ct != ContentType.UNSET:
                self.filter_combo.addItem(ct.value, ct)
        self.filter_combo.currentIndexChanged.connect(self._on_filter_changed)
        top_bar.addWidget(self.filter_combo)
        top_bar.addStretch()
        root_layout.addLayout(top_bar)

        # --- Splitter: TagPanel (left) + file table (right) ---
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter = self.splitter  # local alias, existing code below refers to it by this name
        root_layout.addWidget(splitter)

        self.tag_panel = TagPanel()
        self.tag_panel.apply_requested.connect(self._on_apply_to_selected)
        self.tag_panel.collapseToggleRequested.connect(self._toggle_tag_panel)
        # Deliberately low minimum -- lets the panel be dragged down to a
        # sliver, or fully collapsed (see _toggle_tag_panel()), rather
        # than being locked to a wide fixed range.
        self.tag_panel.setMinimumWidth(24)
        self.tag_panel.setMaximumWidth(440)
        splitter.addWidget(self.tag_panel)

        self.table = QTableWidget()
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

    def _build_menu_bar(self) -> None:
        """Menu bar built via the shared redactor_common menu framework,
        so the top-level shape (File / Import / Operations / Settings /
        Help, in that order, with those exact mnemonics) matches every
        other Redactor project -- this project already used that same
        five-menu shape by convention, but each QAction was previously
        hand-built here rather than going through a shared builder.

        Actions built this way are still reused (never duplicated) on
        the toolbar built right after this in __init__ (see
        _build_toolbar) -- see the actions dict assignment below.
        """
        specs = {
            "File": [
                # NOTE: kept on Ctrl+O (via StandardKey.Open) rather than
                # the family's usual Ctrl+Shift+O "load folder" convention
                # -- this project's only load action IS a folder, unlike
                # cbz/epub/mp3 which distinguish "load files" (Ctrl+O)
                # from "load folder" (Ctrl+Shift+O). Reassigning this
                # would collide with import_subtitles below, already on
                # Ctrl+Shift+O -- left as a known cross-app inconsistency
                # rather than resolved unilaterally; see the 2026-09-13
                # hotkey audit.
                MenuAction("open_folder", "&Open Folder...", self._on_open_folder,
                           shortcut=QKeySequence.StandardKey.Open),
                Separator(),
                MenuAction("save_selected", "&Save Selected", self._on_save_selected, shortcut="Ctrl+S"),
                # NOTE: Ctrl+Shift+S is QKeySequence::SaveAs / cbzredactor's
                # own "Save As..." key -- this project has no distinct
                # Save-As action, but reusing that key for "Save All" here
                # (rather than shortcuts.SAVE_ALL / Ctrl+Shift+A, which
                # cbzredactor's actual Save All uses) is a second known
                # cross-app inconsistency, also left unresolved rather
                # than decided unilaterally; see the 2026-09-13 audit.
                MenuAction("save_all", "Save &All Changed", self._on_save_all, shortcut="Ctrl+Shift+S"),
                Separator(),
                # Quick, direct rename of the one selected file -- matches
                # Explorer's F2 exactly. This project already had the
                # underlying action (see the context menu's "Rename
                # File..." below) but never exposed it as a keyboard
                # shortcut. Distinct from "rename_by_pattern" (Operations
                # menu, Ctrl+E) below -- that's the batch pattern tool.
                MenuAction(
                    "rename_file", "&Rename File...", self.rename_selected_file,
                    shortcut=shortcuts.RENAME_SINGLE_FILE,
                ),
                Separator(),
                # F5 only, not the family's usual F5/Ctrl+R pair -- Ctrl+R
                # is already "Remux Selected to MP4..." in this project's
                # Operations menu (see MenuAction("remux", ...) below);
                # adding it here too would make both ambiguous.
                MenuAction("refresh_list", "Re&fresh List", self._refresh_list, shortcut="F5"),
                Separator(),
                # No explicit shortcut -- Alt+F4 already closes this (or
                # any) plain QMainWindow at the OS level, verified
                # directly (launch, send Alt+F4, confirm the process
                # exits), independent of anything bound here.
                # QKeySequence.StandardKey.Quit (the previous binding)
                # resolves to ZERO actual key bindings on Windows --
                # confirmed via QKeySequence.keyBindings() -- so it was
                # never doing anything anyway.
                MenuAction("exit", "E&xit", self.close),
            ],
            "Import": [
                MenuAction("import_tmdb_movie", "Import Metadata from TMDB (&Movie)...",
                           lambda: self._on_import_tmdb("movie"), shortcut="Ctrl+M"),
                MenuAction("import_tmdb_tv", "Import Metadata from TMDB (&TV Show)...",
                           lambda: self._on_import_tmdb("tv"), shortcut="Ctrl+T"),
                MenuAction("import_tvdb", "Import Metadata from TheTVDB (T&V Show)...",
                           self._on_import_tvdb, shortcut="Ctrl+Shift+T"),
                Separator(),
                MenuAction("import_from_filename", "Import Metadata from &Filename...",
                           self._on_import_metadata_from_filename,
                           shortcut=shortcuts.PARSE_FILENAME_TO_METADATA),
                MenuAction("import_subtitles", "Import &Subtitles from OpenSubtitles...",
                           self._on_import_subtitles, shortcut="Ctrl+Shift+O"),
                Separator(),
                # Brings a different video FORMAT in, converted to join
                # this app's MP4/M4V/MKV-only library -- same shape as
                # mp3redactor's "Import & Convert to MP3..." (its
                # core.mp3_converter), just video instead of audio.
                # Unlike Remux/Convert to MP4 in Operations (which only
                # ever act on files already loaded), this is the one
                # place a non-MP4/M4V/MKV file can enter this app at
                # all.
                MenuAction("import_convert", "Import && &Convert to MP4...",
                           self._on_import_and_convert),
            ],
            "Operations": [
                MenuAction("remux", "&Remux Selected to MP4...", self._on_remux_selected, shortcut="Ctrl+R"),
                MenuAction("convert_to_mp4", "Con&vert Selected to MP4 (H.264)...",
                           self._on_convert_to_mp4, shortcut="Ctrl+Shift+C"),
                MenuAction("rename_by_pattern", "Rena&me/Export by Pattern...",
                           self._on_rename_by_pattern, shortcut=shortcuts.RENAME_EXPORT_BY_PATTERN),
                Separator(),
                MenuAction("case_conversion", "Case &Conversion...", self._on_case_conversion),
                MenuAction(
                    "search_replace", "Search/&Replace...", self._on_search_replace,
                    shortcut=shortcuts.SEARCH_REPLACE,
                ),
                MenuAction("auto_numbering", "Auto-&Numbering...", self._on_auto_numbering),
                Separator(),
                MenuAction("undo", "&Undo", self.undo_last_action, shortcut=shortcuts.UNDO),
                MenuAction("redo", "&Redo", self.redo_last_action, shortcut=shortcuts.REDO),
            ],
            "Settings": [
                MenuAction("locate_tools", "&Locate External Tools...", self._on_locate_tools),
                MenuAction("add_api_keys", "Add External &APIs...", self._on_add_external_apis),
                Separator(),
                MenuAction("add_remove_columns", "Add/Remove &Columns...", self._on_open_column_visibility),
                MenuAction("add_remove_languages", "Add/Remove &Languages...", self._on_open_languages),
                MenuAction("add_remove_genres", "Add/Remove &Genres...", self._on_open_genres),
            ],
            "Help": [
                MenuAction("about", f"&About {APP_NAME}", self._on_show_about, shortcut=shortcuts.HELP),
                MenuAction("changelog", "View &Changelog", self._on_show_changelog),
                MenuAction("credits", "&Credits", self._on_show_credits),
            ],
        }
        actions = build_menu_bar(self, specs)

        # Back-compat: the rest of this file (toolbar) references these
        # as self.<x>_action attributes directly.
        self.open_folder_action = actions["open_folder"]
        self.save_selected_action = actions["save_selected"]
        self.save_all_action = actions["save_all"]
        self.rename_file_action = actions["rename_file"]
        self.undo_action = actions["undo"]
        self.redo_action = actions["redo"]
        self.undo_action.setEnabled(False)
        self.redo_action.setEnabled(False)

    def _build_toolbar(self) -> None:
        """Toolbar beneath the menu bar for the most-used actions (Open
        Folder, Apply, Save Selected, Save All) -- matches the epub
        tool's own menu-bar-plus-toolbar layout. Open Folder and the
        two Save actions reuse the exact same QAction instances already
        created in _build_menu_bar rather than creating parallel ones
        with their own separate triggered connections -- one source of
        truth per command, so a future change to what "Open Folder"
        does only needs updating once. Apply is the one exception:
        defined here directly rather than in _build_menu_bar, since
        (per explicit request, moved here from a plain button inside
        TagPanel) it lives ONLY in the toolbar, not also in a menu --
        putting it in _build_menu_bar would misleadingly imply a menu
        entry exists somewhere that doesn't.

        setMovable(False) keeps it pinned directly under the menu bar
        rather than user-draggable to a window edge or floating --
        matches a fixed toolbar being the simpler, more predictable
        default for an app this size, and avoids "where did my toolbar
        go" confusion after an accidental drag.
        """
        toolbar = QToolBar("Main Toolbar", self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        toolbar.addAction(self.open_folder_action)
        toolbar.addSeparator()

        self.apply_action = make_action(
            self, "&Apply to Selected", lambda: self.tag_panel.apply_pending_changes(),
            shortcut="Ctrl+Return",
            tooltip=(
                "Apply typed changes in the panel to the selected file(s) "
                "-- does not save to disk (Save already applies pending "
                "changes automatically, so this is only needed to stage "
                "changes without saving yet)"
            ),
        )
        toolbar.addAction(self.apply_action)

        toolbar.addAction(self.save_selected_action)
        toolbar.addAction(self.save_all_action)

        toolbar.addSeparator()

        # +/- table-font zoom, matching the epub tool's toolbar control
        # (this project never had one before) -- redactor_common's
        # TableZoomController owns the QAction pair + percentage label;
        # this window just places them.
        self.zoom = TableZoomController(self.table, parent=self)
        toolbar.addAction(self.zoom.zoom_out_action)
        toolbar.addWidget(self.zoom.label)
        toolbar.addAction(self.zoom.zoom_in_action)

        toolbar.addSeparator()

        # Minimize/restore the bulk-edit panel, matching the epub tool's
        # toolbar control (this project never had one before -- the
        # panel's own in-corner button and dragging the splitter handle
        # by hand were the only ways to do this).
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

    def _on_locate_tools(self) -> None:
        """Open the tool-location settings dialog (Settings menu, or
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
        """Add/Edit External APIs (Settings menu) -- lets the user
        enter TMDB/TheTVDB API keys directly rather than needing an
        environment variable or hand-edited settings.ini. Nothing needs
        refreshing after it closes; the key is only read at the moment
        an import is actually attempted.
        """
        dialog = ApiKeysDialog(parent=self)
        dialog.exec()

    def _on_open_column_visibility(self) -> None:
        """Add/Remove Columns (Settings menu) -- redactor_common's shared
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

    def _on_open_languages(self) -> None:
        """Add/Remove Languages (Settings menu) -- refreshes the bulk-
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
        """Add/Remove Genres (Settings menu) -- same immediate-refresh
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
        self._rebuild_table_columns(self.filter_combo.currentData())
        self.tag_panel.refresh_fields()

    # --- Column / filter handling -------------------------------------

    def _rebuild_table_columns(self, content_type_filter: Optional[ContentType]) -> None:
        """Rebuild table columns for the given filter (None = all fields).

        Applies the user's persisted drag-order and hidden-column
        preferences on top of whatever the filter says is relevant --
        the filter decides the CANDIDATE set of columns, the user's
        saved preferences decide their order and which of those
        candidates are actually shown. Changing the filter re-runs this
        (see _on_filter_changed), so persisted preferences survive a
        filter change rather than needing to be re-set every time.
        """
        if content_type_filter is None:
            field_names = list(FIELD_LABELS.keys())
        else:
            from core.video_metadata import fields_for_content_type
            field_names = fields_for_content_type(content_type_filter)

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

    def _on_filter_changed(self) -> None:
        content_type = self.filter_combo.currentData()
        self._rebuild_table_columns(content_type)
        self.tag_panel.set_content_type_filter(content_type)

    # --- Loading files ---------------------------------------------------

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

        recursive = False
        if has_subfolders(folder_path):
            # Only asked when there's actually something to ask about --
            # prompting on every folder open, including ones with no
            # subfolders at all, would just be a pointless extra click.
            reply = QMessageBox.question(
                self, "Include Subfolders?",
                f"\"{folder_path.name}\" contains subfolders. "
                "Include video files from subfolders too?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,  # default: match the epub tool's non-recursive default
            )
            recursive = reply == QMessageBox.StandardButton.Yes

        self._load_folder(folder_path, recursive=recursive)

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
        paths = discover_video_files(folder_path, recursive=recursive)

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
        msg += status_suffix
        self.status_bar.showMessage(msg)

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
            return
        if self._count_dirty() and not self._confirm_discard("refresh the list (discarding unsaved changes)"):
            return

        existing_paths = [str(vf.path) for vf in self.video_files]
        new_paths = find_new_files_in_loaded_folders(
            existing_paths,
            lambda folder: [str(p) for p in discover_video_files(Path(folder), recursive=False)],
        )
        all_paths = [Path(p) for p in existing_paths + new_paths]

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

        Deliberately non-recursive with no subfolder prompt, even if
        the folder has subfolders and was originally loaded
        recursively -- asking a modal question immediately on launch,
        before the user has gotten oriented, would be jarring; a
        recursive re-load is one manual Open Folder away if wanted.
        """
        last_folder = get_setting("general", "last_folder", "")
        if not last_folder:
            return
        folder_path = Path(last_folder)
        if not folder_path.is_dir():
            return
        self._load_folder(folder_path, recursive=False, status_suffix=" (restored from last session)")

    # --- Table rendering ---------------------------------------------------

    def _refresh_table_rows(self) -> None:
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
                    tooltip = vf.save_error or vf.load_error
                    if tooltip:
                        item.setToolTip(tooltip)
                    if row_colors is not None:
                        bg, fg = row_colors
                        item.setBackground(bg)
                        item.setForeground(fg)
                    self.table.setItem(row, col, item)

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
        return None

    def _display_value(self, vf: VideoFile, field_name: str) -> str:
        if field_name == "filename":
            return vf.path.name
        if field_name == "path":
            return str(vf.path)
        if field_name == "status":
            if vf.load_error:
                return "LOAD ERROR"
            if vf.save_error:
                return "SAVE FAILED"
            if vf.dirty:
                return "UNSAVED"
            return "OK"
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
        value = getattr(vf.metadata, field_name, "")
        return "" if value is None else str(value)

    # --- Selection -> TagPanel -------------------------------------------

    def _selected_video_files(self) -> list[VideoFile]:
        files: list[VideoFile] = []
        seen_rows = set()
        for item in self.table.selectedItems():
            if item.row() in seen_rows:
                continue
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
            # to the same name doesn't make sense. Distinct from any
            # future pattern-based batch rename tool: this is the
            # quick, direct fix for one typo at a time -- also
            # reachable by double-clicking the Filename cell (see
            # _on_cell_double_clicked()).
            # Reuses the actual File-menu QAction (F2) rather than
            # building a fresh one -- same object, so this shows the
            # real shortcut hint and can never drift out of sync with
            # it. Only offered for a single file -- renaming several to
            # the same name doesn't make sense.
            if len(files) == 1 and not files[0].load_error:
                items.append(self.rename_file_action)
            items.append(MenuAction(
                "number_episodes", "Number Episodes...", lambda: self._quick_number_episodes(files)
            ))
            return items

        show_table_context_menu(
            self, self.table, pos,
            get_selected_items=self._selected_video_files,
            get_path=lambda vf: vf.path,
            extra_items=extra_items,
        )

    def _on_cell_double_clicked(self, row: int, col: int) -> None:
        if not (0 <= col < len(self._column_order)) or self._column_order[col] != "filename":
            return
        if not (0 <= row < len(self.video_files)):
            return
        vf = self.video_files[row]
        if not vf.load_error:
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
        if prompt_rename_single_file(self, str(vf.path), lambda p: setattr(vf, "path", Path(p))):
            self._refresh_table_rows()

    def rename_selected_file(self) -> None:
        """F2 entry point (Explorer convention: select one item, press
        F2, rename it directly) -- same guard the right-click "Rename
        File..." item uses (exactly one file selected, no load error),
        since F2 and that menu item are the same action reached two
        ways."""
        files = self._selected_video_files()
        if len(files) == 1 and not files[0].load_error:
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
        self.tag_panel.set_collapsed_indicator(self._panel_collapser.is_collapsed())

    def _on_selection_changed(self) -> None:
        selected = self._selected_video_files()
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

    def _on_save_selected(self) -> None:
        # "Hit Save" implies "I meant to Apply first" -- flush any
        # typed-but-not-yet-Applied panel edits onto the selected
        # file(s)' metadata before saving, so Save never silently
        # writes stale data just because Apply wasn't clicked separately.
        self.tag_panel.apply_pending_changes()

        selected = self._selected_video_files()
        if not selected:
            self.status_bar.showMessage("No files selected")
            return
        saveable, skipped = self._split_saveable(selected)
        if not saveable:
            self.status_bar.showMessage(
                f"Nothing to save -- all {len(skipped)} selected file(s) have load errors"
            )
            return
        self._save_files(saveable, skipped_error_count=len(skipped))

    def _on_save_all(self) -> None:
        # Same implicit-Apply-first reasoning as _on_save_selected --
        # done before computing the dirty list, since applying pending
        # panel changes can itself be what makes a file dirty.
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

        if failed:
            msg = f"Saved {succeeded} file(s), {len(failed)} failed{skip_note}{cancel_note}"
            self.status_bar.showMessage(msg)
            details = _error_details([f"{vf.path.name}: {vf.save_error}" for vf in failed])
            QMessageBox.warning(self, "Some files failed to save", details)
        else:
            self.status_bar.showMessage(f"Saved {succeeded} file(s){skip_note}{cancel_note}")

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
        super().closeEvent(event)

    # --- Applying bulk edits ----------------------------------------------

    def _on_apply_to_selected(self, changed_fields: dict[str, object]) -> None:
        if not changed_fields:
            # Nothing was actually touched -- marking every selected
            # file dirty anyway (the previous behavior here) would
            # falsely show files as having unsaved changes when
            # nothing was ever applied to them. Real bug, caught while
            # moving the Apply button to the toolbar and reconsidering
            # what triggering it with nothing pending should do.
            return
        selected = self._selected_video_files()
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
                details = get_movie_details(candidate.tmdb_id)
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
                    image_bytes = download_poster(poster_path)
                    vf.save_poster_sidecar(image_bytes)
                    poster_saved += 1
                except TMDBError as e:
                    # A poster failure doesn't undo the metadata import
                    # that already succeeded -- collected alongside
                    # fetch failures for the end-of-batch summary rather
                    # than interrupting the loop with its own dialog.
                    fetch_failures.append((vf, f"poster download failed: {e}"))

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
            show_details = get_tv_show_details(candidate.tmdb_id)
        except TMDBError as e:
            QMessageBox.warning(self, "TMDB Fetch Failed", str(e))
            return

        poster_path = show_details.pop("_poster_path", None)
        poster_bytes = None
        poster_error = ""
        if poster_path:
            try:
                poster_bytes = download_poster(poster_path)
            except TMDBError as e:
                poster_error = str(e)

        self._push_undo("TMDB Import", files)
        for vf in files:
            vf.metadata.content_type = ContentType.TV
            for field_name, value in show_details.items():
                setattr(vf.metadata, field_name, value)
            vf.dirty = True
            if poster_bytes:
                vf.save_poster_sidecar(poster_bytes)

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
                episode_details = get_tv_episode_details(
                    candidate.tmdb_id, episode_dialog.selected_season,
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
        if poster_bytes:
            parts.append("poster saved")
        elif poster_error:
            parts.append(f"poster download failed: {poster_error}")
        if skipped_load_errors:
            parts.append(f"{skipped_load_errors} skipped (load errors)")
        if episode_failures:
            parts.append(f"{len(episode_failures)} episode lookup(s) failed")
        self.status_bar.showMessage(", ".join(parts))

        if episode_failures:
            details_text = _error_details([f"{vf.path.name}: {err}" for vf, err in episode_failures])
            QMessageBox.warning(self, "Some episode lookups failed", details_text)

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
            episode_details = get_episode_details(
                tvdb_id, episode_dialog.selected_season,
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

        self._push_undo("TheTVDB Import", [vf])
        try:
            details = get_series_details(candidate.tvdb_id)
            vf.metadata.content_type = ContentType.TV
            self._apply_tvdb_episode_details(candidate.tvdb_id, details, vf.path.stem)
        except TVDBError as e:
            QMessageBox.warning(self, "TheTVDB Fetch Failed", str(e))
            return

        poster_url = details.pop("_poster_path", None)
        for field_name, value in details.items():
            setattr(vf.metadata, field_name, value)
        vf.dirty = True

        status_msg = "Imported metadata from TheTVDB -- not yet saved to disk"
        if "episode_number" not in details:
            status_msg += " (show-level only -- episode selection was skipped)"
        if poster_url:
            try:
                image_bytes = download_image(poster_url)
                saved = vf.save_poster_sidecar(image_bytes)
                status_msg += f"; poster saved as {saved.name}"
            except TVDBError as e:
                status_msg += f"; poster download failed: {e}"

        self._refresh_table_rows()
        if vf in self._selected_video_files():
            self._on_selection_changed()
        self.status_bar.showMessage(status_msg)

    # --- Remux ---------------------------------------------------------

    def _on_remux_selected(self) -> None:
        """Remux selected MKV files to MP4 (batch-capable, -c copy so
        it's fast/lossless -- no re-encode). For each successful remux,
        asks whether to delete the original MKV (per-file confirmation,
        with Yes/No-to-All shortcuts so a large batch doesn't demand
        20 individual clicks) and auto-adds the new MP4 as a row in the
        table.
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

        # None = ask per file; True/False = "to all" choice already made
        delete_all_choice: Optional[bool] = None
        succeeded: list[tuple[VideoFile, Path]] = []
        failed: list[tuple[VideoFile, str]] = []
        skipped_existing: list[VideoFile] = []
        deletion_failures: list[tuple[VideoFile, str]] = []
        new_files: list[VideoFile] = []
        removed_originals: list[VideoFile] = []

        for vf in mkv_files:
            output_path = vf.path.with_suffix(".mp4")
            if output_path.exists():
                # Refuse to overwrite an existing file rather than guess
                # whether it's unrelated or a leftover from a prior remux
                # -- same "deliberate action, fail clearly" reasoning as
                # rename_book_file's collision handling in the epub tool.
                skipped_existing.append(vf)
                continue

            ok, stderr = remux_to_mp4(str(vf.path), str(output_path))
            if not ok:
                failed.append((vf, stderr.strip() or "ffmpeg remux failed"))
                continue

            succeeded.append((vf, output_path))

            new_vf = VideoFile(path=output_path)
            new_vf.load()
            new_files.append(new_vf)

            if delete_all_choice is not None:
                delete_original: object = delete_all_choice
            else:
                delete_original = self._confirm_delete_original(vf, output_path)
            if isinstance(delete_original, tuple):
                delete_original, remembered_choice = delete_original
                delete_all_choice = remembered_choice

            if delete_original:
                try:
                    vf.path.unlink()
                    removed_originals.append(vf)
                except OSError as e:
                    deletion_failures.append((vf, str(e)))

        self.video_files.extend(new_files)
        if removed_originals:
            removed_set = set(id(vf) for vf in removed_originals)
            self.video_files = [vf for vf in self.video_files if id(vf) not in removed_set]

        self._refresh_table_rows()

        parts = [f"Remuxed {len(succeeded)} file(s)"]
        if failed:
            parts.append(f"{len(failed)} failed")
        if skipped_existing:
            parts.append(f"{len(skipped_existing)} skipped (MP4 already exists)")
        if non_mkv_count:
            parts.append(f"{non_mkv_count} non-MKV selection(s) ignored")
        if deletion_failures:
            parts.append(f"{len(deletion_failures)} original(s) could not be deleted")
        self.status_bar.showMessage(", ".join(parts))

        if failed:
            details = _error_details([f"{vf.path.name}: {err}" for vf, err in failed])
            QMessageBox.warning(self, "Some files failed to remux", details)
        if deletion_failures:
            details = _error_details([f"{vf.path.name}: {err}" for vf, err in deletion_failures])
            QMessageBox.warning(self, "Some originals could not be deleted", details)

    def _confirm_delete_original(self, vf: VideoFile, output_path: Path):
        """Ask whether to delete the original MKV after a successful
        remux. Returns a plain bool normally; returns (bool, bool) when
        a "to all" button is clicked, so the caller can remember the
        choice for the rest of the batch without asking again.
        """
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Delete Original MKV?")
        box.setText(
            f"Remuxed to {output_path.name}.\n\nDelete the original file {vf.path.name}?"
        )
        btn_yes = box.addButton("Yes", QMessageBox.ButtonRole.YesRole)
        btn_no = box.addButton("No", QMessageBox.ButtonRole.NoRole)
        btn_yes_all = box.addButton("Yes to All", QMessageBox.ButtonRole.AcceptRole)
        btn_no_all = box.addButton("No to All", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()

        if clicked == btn_yes:
            return True
        if clicked == btn_no:
            return False
        if clicked == btn_yes_all:
            return (True, True)
        if clicked == btn_no_all:
            return (False, False)
        return False  # dialog dismissed without a button (e.g. Esc) -- default to not deleting

    def _run_transcode_jobs(
        self, jobs: list[tuple[Path, Path]], settings, title: str
    ) -> dict[int, tuple[bool, str]]:
        """Runs `jobs` on a _TranscodeWorker thread behind the shared
        progress dialog (fixed width, elided per-file label) and returns
        {job index: (ok, message)} -- an index missing from the result
        never started because the batch was cancelled first. Shared by
        Convert to MP4 and Import & Convert, which each hand-rolled an
        identical copy of this before.

        threshold=1: even a single encode takes minutes, so the dialog
        (and its Cancel button) always shows."""
        worker = _TranscodeWorker(jobs, settings, parent=self)
        results: dict[int, tuple[bool, str]] = {}
        with ProgressReporter(self, len(jobs), "Starting...", threshold=1, title=title) as reporter:
            def on_started(i: int, name: str) -> None:
                reporter.set_label(f"Converting {name}... ({i + 1}/{len(jobs)})")
                reporter.set_value(i, pump=False)

            def on_finished(i: int, ok: bool, message: str) -> None:
                results[i] = (ok, message)

            def on_cancel() -> None:
                worker.cancel_event.set()
                reporter.set_label("Cancelling (finishing current file)...")

            worker.file_started.connect(on_started)
            worker.file_finished.connect(on_finished)
            reporter.connect_cancel(on_cancel)

            # The dialog's modality pumps clicks/paint events, but not
            # "wait for this thread to finish" -- a local event loop tied
            # to the worker's `finished` signal blocks here without
            # freezing the UI (the encode itself runs on the worker).
            wait_loop = QEventLoop()
            worker.finished.connect(wait_loop.quit)
            worker.start()
            wait_loop.exec()
        return results

    def _on_convert_to_mp4(self) -> None:
        """Re-encode selected files to H.264/AAC MP4 (batch-capable),
        using the CRF/audio-bitrate/thread defaults from Tool Settings
        (core/transcode_settings.py). Unlike Remux (-c copy, lossless,
        MKV-only), this applies to any selected file and is a genuine
        re-encode -- slower, and a generation/quality loss versus the
        source, so unlike _on_remux_selected there is deliberately NO
        "delete the original?" prompt here: encouraging a user to throw
        away their only lossless copy right after a lossy conversion is
        the wrong default, even though remux's equivalent prompt is safe
        (a remux loses nothing).

        Runs off the GUI thread via _TranscodeWorker + the shared progress dialog
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
            if output_path.exists():
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
            "deleted automatically. Adjust these defaults via "
            "Settings > Locate External Tools.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        results = self._run_transcode_jobs(jobs, settings, "Converting to MP4")

        new_files: list[VideoFile] = []
        succeeded = 0
        failed: list[tuple[Path, str]] = []
        cancelled_count = 0
        for i, (input_path, output_path) in enumerate(jobs):
            result = results.get(i)
            if result is None:
                cancelled_count += 1  # never started -- batch was cancelled first
                continue
            ok, message = result
            if ok:
                succeeded += 1
                new_vf = VideoFile(path=output_path)
                new_vf.load()
                new_files.append(new_vf)
            elif message == "Cancelled":
                cancelled_count += 1
            else:
                failed.append((output_path, message))

        self.video_files.extend(new_files)
        self._refresh_table_rows()

        parts = [f"Converted {succeeded} file(s)"]
        if failed:
            parts.append(f"{len(failed)} failed")
        if cancelled_count:
            parts.append(f"{cancelled_count} cancelled")
        if skipped_existing:
            parts.append(f"{len(skipped_existing)} skipped (output already exists)")
        self.status_bar.showMessage(", ".join(parts))

        if failed:
            details = _error_details(
                [f"{path.name}: {err.strip() or 'ffmpeg conversion failed'}" for path, err in failed]
            )
            QMessageBox.warning(self, "Some files failed to convert", details)

    # --- Import & Convert --------------------------------------------------

    def _on_import_and_convert(self) -> None:
        """Import menu > "Import & Convert to MP4..." -- brings a
        non-MP4/M4V/MKV video file (AVI/MOV/WMV/FLV/WebM/MPG/...) into
        the library by re-encoding it to H.264/AAC MP4 via ffmpeg
        (core.ffmpeg_backend.transcode_to_mp4), same directory, same
        base filename. The only place a foreign format can enter this
        app at all -- Open Folder only ever picks up .mp4/.m4v/.mkv
        (core.video_file.SUPPORTED_EXTENSIONS).

        Deliberately ADDITIVE to self.video_files, unlike Open Folder's
        replace-wholesale semantics -- same reasoning as mp3redactor's
        own Import & Convert (see that project's gui/main_window.py).
        Reuses the exact same _TranscodeWorker + Tool Settings
        (CRF/audio bitrate/threads) as Operations > Convert Selected to
        MP4 (H.264) -- transcode_to_mp4() is already format-agnostic on
        its input (whatever `ffmpeg -i` can read), so nothing in
        core/ffmpeg_backend.py needed to change to support this, only
        what the GUI lets a user pick.
        """
        extensions_filter = " ".join(f"*{ext}" for ext in sorted(IMPORTABLE_EXTENSIONS))
        last_folder = get_setting("general", "last_folder", "")
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Import & Convert to MP4",
            last_folder,
            f"Video Files ({extensions_filter})",
        )
        if not paths:
            return

        jobs: list[tuple[Path, Path]] = []
        skipped_existing: list[Path] = []
        for raw_path in paths:
            src = Path(raw_path)
            dest = src.with_suffix(".mp4")
            if dest.exists():
                # Refuse to silently clobber an existing file of that
                # name -- same safety-first instinct as Convert Selected
                # to MP4's own existing-output check below.
                skipped_existing.append(dest)
                continue
            jobs.append((src, dest))

        if skipped_existing:
            names = summarize_errors([p.name for p in skipped_existing])
            proceed = QMessageBox.question(
                self,
                "Some Files Already Exist",
                f"{len(skipped_existing)} file(s) already have an .mp4 of the same name in "
                f"that folder and will be skipped (not overwritten):\n\n{names}\n\n"
                f"Convert the remaining {len(jobs)} file(s)?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            )
            if proceed != QMessageBox.StandardButton.Yes:
                return

        if not jobs:
            return

        settings = get_transcode_settings()
        thread_note = f", {settings.threads} threads" if settings.threads else ""
        confirm = QMessageBox.question(
            self, "Import & Convert to MP4",
            f"Convert {len(jobs)} file(s) to H.264/AAC MP4 "
            f"(CRF {settings.crf}, audio {settings.audio_bitrate}{thread_note})?\n\n"
            "Originals are never deleted automatically. Adjust these "
            "defaults via Settings > Locate External Tools.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        results = self._run_transcode_jobs(jobs, settings, "Importing & Converting")

        set_setting("general", "last_folder", str(Path(paths[0]).parent))

        new_files: list[VideoFile] = []
        succeeded = 0
        failed: list[tuple[Path, str]] = []
        cancelled_count = 0
        for i, (input_path, output_path) in enumerate(jobs):
            result = results.get(i)
            if result is None:
                cancelled_count += 1  # never started -- batch was cancelled first
                continue
            ok, message = result
            if ok:
                succeeded += 1
                new_vf = VideoFile(path=output_path)
                new_vf.load()
                new_files.append(new_vf)
            elif message == "Cancelled":
                cancelled_count += 1
            else:
                failed.append((output_path, message))

        self.video_files.extend(new_files)
        self._refresh_table_rows()

        parts = [f"Imported and converted {succeeded} file(s)"]
        if failed:
            parts.append(f"{len(failed)} failed")
        if cancelled_count:
            parts.append(f"{cancelled_count} cancelled")
        if skipped_existing:
            parts.append(f"{len(skipped_existing)} skipped (output already exists)")
        self.status_bar.showMessage(", ".join(parts))

        if failed:
            details = _error_details(
                [f"{path.name}: {err.strip() or 'ffmpeg conversion failed'}" for path, err in failed]
            )
            QMessageBox.warning(self, "Some files failed to convert", details)
        elif succeeded:
            QMessageBox.information(
                self, "Import Complete", f"Converted and loaded {succeeded} file(s)."
            )

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

        dialog = SubtitleSearchDialog(str(vf.path), language="en", parent=self)
        if not dialog.exec() or dialog.selected_candidate is None:
            return
        candidate = dialog.selected_candidate

        try:
            subtitle_text = download_subtitle_text(candidate.file_id)
        except OpenSubtitlesError as e:
            QMessageBox.warning(self, "Subtitle Download Failed", str(e))
            return

        saved_path = vf.save_subtitle_sidecar(subtitle_text, language=candidate.language)

        sync_note = "exact match" if candidate.hash_matched else "title match -- sync not guaranteed"
        self.status_bar.showMessage(f"Saved subtitle to {saved_path.name} ({sync_note})")

    # --- Filename patterns -------------------------------------------------

    def _batch_targets(self, verb: str) -> list[VideoFile]:
        """The selection, or every loaded file if nothing is selected --
        the family's convention for whole-batch operations. Files that
        failed to load are excluded; an empty list means "nothing to do"
        and the user has already been told why."""
        selected = self._selected_video_files()
        targets = [vf for vf in (selected or self.video_files) if not vf.load_error]
        if not targets:
            QMessageBox.information(self, "No Files", f"Load some files first (or select the ones to {verb}).")
        return targets

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
            parent=self,
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return

        save_pattern_to_history(dialog.pattern_edit.text())
        export_mode = dialog.is_export_mode()
        done = 0
        errors: list[str] = []
        for vf, old_path, new_path in dialog.planned_renames():
            if os.path.normcase(os.path.abspath(old_path)) == os.path.normcase(os.path.abspath(new_path)):
                continue
            try:
                if export_mode:
                    shutil.copy2(old_path, new_path)
                else:
                    os.rename(old_path, new_path)
                    vf.path = Path(new_path)
                done += 1
            except OSError as exc:
                errors.append(f"{os.path.basename(old_path)}: {exc}")

        self._refresh_table_rows()
        verb = "Exported" if export_mode else "Renamed"
        self.status_bar.showMessage(f"{verb} {done} file(s)")
        if errors:
            QMessageBox.warning(self, "Some files failed", _error_details(errors))

    def _on_import_metadata_from_filename(self) -> None:
        """Extract metadata from filenames into staged (unsaved) fields,
        via redactor_common's shared ParseFilenameDialog. Season/Episode
        #/Rating only match digits (so "S01E03" parses cleanly) and lose
        their filename zero-padding."""
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
            parent=self,
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return

        save_pattern_to_history(dialog.pattern_edit.text())
        changes = dialog.accepted_changes()
        if not changes:
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

    # --- Batch text operations (Operations menu) ------------------------

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
        for index, new_stem in changes.items():
            vf = targets[index]
            new_path = vf.path.with_name(new_stem + vf.path.suffix)
            try:
                if new_path.exists() and new_path != vf.path:
                    raise FileExistsError(f"{new_path.name} already exists")
                os.rename(vf.path, new_path)
                vf.path = new_path
                renamed += 1
            except OSError as exc:
                errors.append(f"{vf.path.name}: {exc}")
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
        Operations -> Auto-Numbering... instead. Undoable (Ctrl+Z).
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
            targets, fields, _field_text, lambda vf: vf.path.name, item_noun="file", parent=self,
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
        vf.metadata, vf.dirty = copy.deepcopy(snapshot[0]), snapshot[1]

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
        CreditsDialog(str(CREDITS_PATH), parent=self).exec()
