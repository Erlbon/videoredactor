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
import json

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QSplitter,
    QTableWidget, QTableWidgetItem, QComboBox, QLabel, QFileDialog,
    QStatusBar, QMessageBox, QToolBar, QProgressDialog, QApplication,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QAction, QKeySequence, QIcon

from core.video_file import VideoFile, discover_video_files, has_subfolders
from core.video_metadata import ContentType, EDITABLE_FIELDS
from core.tmdb_client import (
    get_movie_details, get_tv_show_details, get_tv_episode_details,
    download_poster, TMDBError,
)
from core.tvdb_client import get_series_details, get_episode_details, download_image, TVDBError
from core.release_name_parser import parse_release_name
from core.ffmpeg_backend import remux_to_mp4
from core.opensubtitles_client import download_subtitle_text, OpenSubtitlesError
from core.table_settings import merge_column_order, is_column_visible, sanitize_hidden_fields
from core.format_helpers import format_duration, format_file_size
from core.config import get_setting, set_setting
from redactor_common.gui.menu_builder import MenuAction, Separator, build_menu_bar
from redactor_common.gui.context_menu import show_table_context_menu
from redactor_common.gui.column_menu import show_column_header_context_menu
from redactor_common.gui.collapsible_splitter import SplitterPaneCollapser
from redactor_common.gui.zoom_toolbar import TableZoomController
from redactor_common.gui.about_dialog import AboutDialog, ChangelogDialog, CreditsDialog
from redactor_common.core.version import REDACTOR_COMMON_REPO_URL, REDACTOR_COMMON_VERSION
from gui.tag_panel import TagPanel, FIELD_LABELS
from gui.tmdb_search_dialog import TMDBSearchDialog
from gui.tmdb_episode_picker_dialog import TVEpisodePickerDialog
from gui.tvdb_search_dialog import TVDBSearchDialog
from gui.tvdb_episode_picker_dialog import TVDBEpisodePickerDialog
from gui.subtitle_search_dialog import SubtitleSearchDialog
from gui.rename_pattern_dialog import RenameByPatternDialog
from gui.parse_filename_dialog import ParseFilenameDialog
from gui.case_conversion_dialog import CaseConversionDialog
from gui.search_replace_dialog import SearchReplaceDialog
from gui.auto_numbering_dialog import AutoNumberingDialog
from gui.tool_settings_dialog import ToolSettingsDialog
from gui.column_visibility_dialog import ColumnVisibilityDialog
from gui.vocabulary_editor_dialog import VocabularyEditorDialog
from gui.api_keys_dialog import ApiKeysDialog
from core.controlled_vocab import (
    get_genre_options, add_genre_option, remove_genre_option,
    get_language_options, add_language_option, remove_language_option,
)
from core.version import APP_REPO_URL, APP_VERSION, RELEASE_LABEL
from core.external_tools import missing_tools

# Assets live one level up from gui/, resolved relative to this file so
# it works both from source and from a PyInstaller-frozen build (where
# datas=[("assets/icon.ico", "assets")] in the spec file places it
# alongside the frozen app, not necessarily at the same relative path
# as the source tree -- sys._MEIPASS is PyInstaller's extraction dir
# when frozen).
import sys
if getattr(sys, "frozen", False):
    ASSETS_DIR = Path(sys._MEIPASS) / "assets"
    PROJECT_ROOT = Path(sys._MEIPASS)
else:
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    ASSETS_DIR = PROJECT_ROOT / "assets"
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
TECHNICAL_COLUMNS = ["duration_seconds", "resolution", "size_bytes"]
TECHNICAL_LABELS = {
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
DIRTY_BG = QColor(255, 244, 200)
DIRTY_FG = QColor(70, 55, 0)      # dark brown-gold, readable on the tint above
ERROR_BG = QColor(255, 210, 210)
ERROR_FG = QColor(90, 20, 20)     # dark red, readable on the tint above

# Selection highlight -- same "own both colors as a fixed pair" fix,
# applied to a DIFFERENT problem than DIRTY/ERROR above: Qt's row
# SELECTION highlight is a separate rendering layer from a
# QTableWidgetItem's own background/foreground (selection typically
# overrides per-item colors while a row is selected), and defaults to
# whatever the OS/Qt style's selection palette is -- which, same as
# the DIRTY/ERROR case, isn't guaranteed to contrast well against a
# dark theme just because it happens to work on a light one. Fixed
# navy background + white text verified at 11.6:1 contrast (WCAG AAA
# for normal text is 7:1), applied via stylesheet rather than trusted
# to the theme default. A row that's BOTH dirty/error AND selected
# will show this selection color while selected (Qt's selection layer
# takes visual priority over the item's own background either way,
# regardless of this fix) -- the Status column's text (UNSAVED/LOAD
# ERROR/etc) still conveys the state even when the tint is temporarily
# not visible, so nothing is actually lost, just not double-shown.
SELECTION_BG = "#14327D"
SELECTION_FG = "#FFFFFF"
TABLE_SELECTION_STYLESHEET = f"""
    QTableWidget::item:selected {{
        background-color: {SELECTION_BG};
        color: {SELECTION_FG};
    }}
"""


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"The Ʌideo Redactor ({RELEASE_LABEL})")
        self.resize(1200, 700)

        if ICON_PATH.exists():
            self.setWindowIcon(QIcon(str(ICON_PATH)))
        # No else/warning here -- a missing icon file shouldn't block the
        # app from launching; Qt just falls back to no icon silently,
        # which is the right degrade for something this cosmetic.

        self.video_files: list[VideoFile] = []
        self._column_order: list[str] = []  # populated in _rebuild_table_columns
        self._suppress_column_signals = False  # True while _rebuild_table_columns
        # is programmatically applying persisted widths/visibility, so those
        # calls don't get misread as user actions and re-saved redundantly
        # (or, for a hidden column's width momentarily reporting as 0,
        # incorrectly overwrite a perfectly good persisted width).

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
        self.table.setStyleSheet(TABLE_SELECTION_STYLESHEET)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
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
                MenuAction("open_folder", "&Open Folder...", self._on_open_folder,
                           shortcut=QKeySequence.StandardKey.Open),
                Separator(),
                MenuAction("save_selected", "&Save Selected", self._on_save_selected, shortcut="Ctrl+S"),
                MenuAction("save_all", "Save &All Changed", self._on_save_all, shortcut="Ctrl+Shift+S"),
                Separator(),
                MenuAction("exit", "E&xit", self.close, shortcut=QKeySequence.StandardKey.Quit),
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
                           self._on_import_metadata_from_filename, shortcut="Ctrl+Shift+F"),
                MenuAction("import_subtitles", "Import &Subtitles from OpenSubtitles...",
                           self._on_import_subtitles, shortcut="Ctrl+Shift+O"),
            ],
            "Operations": [
                MenuAction("remux", "&Remux Selected to MP4...", self._on_remux_selected, shortcut="Ctrl+R"),
                MenuAction("rename_by_pattern", "Rena&me/Export by Pattern...",
                           self._on_rename_by_pattern, shortcut="Ctrl+Shift+R"),
                Separator(),
                MenuAction("case_conversion", "Case &Conversion...", self._on_case_conversion),
                MenuAction("search_replace", "Search/&Replace...", self._on_search_replace),
                MenuAction("auto_numbering", "Auto-&Numbering...", self._on_auto_numbering),
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
                MenuAction("about", "&About The \u0245ideo Redactor", self._on_show_about),
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

        self.apply_action = QAction("&Apply to Selected", self)
        self.apply_action.setShortcut("Ctrl+Return")
        self.apply_action.setToolTip(
            "Apply typed changes in the panel to the selected file(s) "
            "-- does not save to disk (Save already applies pending "
            "changes automatically, so this is only needed to stage "
            "changes without saving yet)"
        )
        self.apply_action.triggered.connect(lambda: self.tag_panel.apply_pending_changes())
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
        toggle_panel_action = QAction("Panel", self)
        toggle_panel_action.setToolTip("Minimize or restore the bulk-edit panel")
        toggle_panel_action.triggered.connect(self._toggle_tag_panel)
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
        """Add/Remove Columns (Settings menu) -- opens the same
        ColumnVisibilityDialog the table header's right-click menu
        already exposes, reading/writing the exact same persisted
        hidden-columns state. Refreshes both the table and the bulk-
        edit panel if anything actually changed, per the same "hiding
        a column also hides its panel field" behavior established
        earlier.
        """
        dialog = ColumnVisibilityDialog(
            list(COLUMN_LABEL_LOOKUP.items()),
            self._load_hidden_fields,
            lambda hidden: set_setting("table", "hidden_columns", ",".join(sorted(hidden))),
            parent=self,
        )
        dialog.exec()
        if dialog.changed:
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
        self.video_files = []

        progress = None
        cancelled = False
        if paths:
            # setMinimumDuration means this only actually appears if
            # loading takes longer than the threshold -- a typical
            # small folder finishes before it ever shows, so this adds
            # zero visual noise for the common case while still
            # covering the "heavy folder looks frozen" complaint this
            # was built for. Determinate (not busy/indeterminate) since
            # the total file count is already known up front.
            progress = QProgressDialog("Loading video files...", "Cancel", 0, len(paths), self)
            progress.setWindowModality(Qt.WindowModality.WindowModal)
            progress.setWindowTitle("Loading Folder")
            progress.setMinimumDuration(400)

        for i, p in enumerate(paths):
            if progress is not None:
                if progress.wasCanceled():
                    cancelled = True
                    break
                progress.setLabelText(f"Loading {p.name}...")
                progress.setValue(i)
                # QProgressDialog doesn't pump the event loop on its
                # own -- without this, the dialog itself would be just
                # as frozen-looking as the table it's meant to explain,
                # and the Cancel button wouldn't respond either.
                QApplication.processEvents()
            vf = VideoFile(path=p)
            vf.load()
            self.video_files.append(vf)

        if progress is not None:
            progress.setValue(len(paths))

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
        self.table.setRowCount(len(self.video_files))
        for row, vf in enumerate(self.video_files):
            row_colors = self._row_colors(vf)
            for col, field_name in enumerate(self._column_order):
                text = self._display_value(vf, field_name)
                item = QTableWidgetItem(text)
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

    def _row_colors(self, vf: VideoFile) -> Optional[tuple[QColor, QColor]]:
        """Returns a (background, foreground) pair, or None for a
        normal/unhighlighted row (which keeps the theme's own default
        colors for both, exactly as before -- only highlighted rows get
        an explicit, theme-independent color pair). Error takes
        priority over dirty -- a file that failed to save is more
        urgent to notice than one with merely-unsaved edits.
        """
        if vf.load_error or vf.save_error:
            return (ERROR_BG, ERROR_FG)
        if vf.dirty:
            return (DIRTY_BG, DIRTY_FG)
        return None

    def _display_value(self, vf: VideoFile, field_name: str) -> str:
        if field_name == "filename":
            return vf.path.name
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
        show_table_context_menu(
            self, self.table, pos,
            get_selected_items=self._selected_video_files,
            get_path=lambda vf: vf.path,
        )

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

        NOTE: this generates synchronously on the GUI thread the first
        time a given file is previewed (subsequent selections of the same
        file hit VideoFile's on-disk cache and return instantly). For a
        very large/slow-to-seek file this could cause a brief UI pause --
        worth revisiting with a background thread if that turns out to be
        noticeable in practice, but not optimizing preemptively here.
        """
        if len(selected) != 1:
            self.tag_panel.set_preview_image(None)
            self.tag_panel.preview_label.setText(f"{len(selected)} files selected")
            return

        vf = selected[0]
        if vf.load_error:
            self.tag_panel.set_preview_image(None)
            return

        thumb_path = vf.get_thumbnail()
        self.tag_panel.set_preview_image(str(thumb_path) if thumb_path else None)

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

        Shows the same progress dialog pattern _load_folder() already
        uses for Open Folder -- each vf.save() now does a write PLUS an
        immediate verify-read-back (see core/video_file.py), roughly
        doubling the I/O cost per file since that safety net was added,
        which made a multi-file save look exactly as "frozen" as a
        heavy folder load used to before that dialog existed.
        """
        succeeded = 0
        failed: list[VideoFile] = []

        progress = None
        cancelled = False
        if files:
            # Same setMinimumDuration threshold as _load_folder -- only
            # actually appears if saving takes long enough to matter,
            # so a quick single-file save shows nothing extra.
            progress = QProgressDialog("Saving files...", "Cancel", 0, len(files), self)
            progress.setWindowModality(Qt.WindowModality.WindowModal)
            progress.setWindowTitle("Saving")
            progress.setMinimumDuration(400)

        for i, vf in enumerate(files):
            if progress is not None:
                if progress.wasCanceled():
                    cancelled = True
                    break
                progress.setLabelText(f"Saving {vf.path.name}...")
                progress.setValue(i)
                QApplication.processEvents()
            vf.save()
            if vf.save_error:
                failed.append(vf)
            else:
                succeeded += 1

        if progress is not None:
            progress.setValue(len(files))

        self._refresh_table_rows()

        skip_note = f", {skipped_error_count} skipped (load errors)" if skipped_error_count else ""
        not_attempted = len(files) - succeeded - len(failed)
        cancel_note = f", cancelled -- {not_attempted} file(s) not yet attempted" if cancelled else ""

        if failed:
            msg = f"Saved {succeeded} file(s), {len(failed)} failed{skip_note}{cancel_note}"
            self.status_bar.showMessage(msg)
            details = "\n".join(f"{vf.path.name}: {vf.save_error}" for vf in failed)
            QMessageBox.warning(self, "Some files failed to save", details)
        else:
            self.status_bar.showMessage(f"Saved {succeeded} file(s){skip_note}{cancel_note}")

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
            details_text = "\n".join(f"{vf.path.name}: {err}" for vf, err in fetch_failures)
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
            details_text = "\n".join(f"{vf.path.name}: {err}" for vf, err in episode_failures)
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
            details = "\n".join(f"{vf.path.name}: {err}" for vf, err in failed)
            QMessageBox.warning(self, "Some files failed to remux", details)
        if deletion_failures:
            details = "\n".join(f"{vf.path.name}: {err}" for vf, err in deletion_failures)
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

    def _on_rename_by_pattern(self) -> None:
        """Batch rename/export by pattern, working on the current
        selection (or ALL loaded files if nothing's selected -- matches
        the epub tool's own Rename/Export tool, which operates on the
        full loaded set by default rather than requiring an explicit
        select-all first for what's usually a whole-batch operation).
        """
        selected = self._selected_video_files()
        targets = selected if selected else self.video_files
        if not targets:
            QMessageBox.information(self, "No Files", "Load some files first.")
            return

        dialog = RenameByPatternDialog(targets, parent=self)
        if dialog.exec():
            self._refresh_table_rows()
            self.status_bar.showMessage(f"Renamed {dialog.renamed_count} file(s)")

    def _on_import_metadata_from_filename(self) -> None:
        """Extract metadata from filenames into staged (unsaved) fields
        (renamed from "Parse Filename to Metadata" per explicit
        request). Same selection-or-all-files scope as rename by
        pattern.
        """
        selected = self._selected_video_files()
        targets = selected if selected else self.video_files
        if not targets:
            QMessageBox.information(self, "No Files", "Load some files first.")
            return

        dialog = ParseFilenameDialog(targets, parent=self)
        if dialog.exec():
            self._refresh_table_rows()
            if self._selected_video_files():
                self._on_selection_changed()  # refresh panel if a parsed file is still selected
            self.status_bar.showMessage(
                f"Imported metadata from filename for {dialog.matched_count} file(s) -- not yet saved to disk"
            )

    # --- Batch text operations (Operations menu) ------------------------

    def _on_case_conversion(self) -> None:
        """Batch case conversion for a chosen text field, staged
        (unsaved) onto the selected files. Same selection-or-all-files
        scope as rename by pattern.
        """
        selected = self._selected_video_files()
        targets = selected if selected else self.video_files
        if not targets:
            QMessageBox.information(self, "No Files", "Load some files first.")
            return

        dialog = CaseConversionDialog(targets, parent=self)
        if dialog.exec():
            self._refresh_table_rows()
            if self._selected_video_files():
                self._on_selection_changed()
            self.status_bar.showMessage(
                f"Converted case for {dialog.converted_count} file(s) -- not yet saved to disk"
            )

    def _on_search_replace(self) -> None:
        """Batch find-and-replace within a chosen text field, staged
        (unsaved) onto the selected files. Same selection-or-all-files
        scope as rename by pattern.
        """
        selected = self._selected_video_files()
        targets = selected if selected else self.video_files
        if not targets:
            QMessageBox.information(self, "No Files", "Load some files first.")
            return

        dialog = SearchReplaceDialog(targets, parent=self)
        if dialog.exec():
            self._refresh_table_rows()
            if self._selected_video_files():
                self._on_selection_changed()
            self.status_bar.showMessage(
                f"Replaced text in {dialog.replaced_count} file(s) -- not yet saved to disk"
            )

    def _on_auto_numbering(self) -> None:
        """Batch sequential-number assignment into a chosen field,
        staged (unsaved) onto the selected files, in their current
        selection/table order. Same selection-or-all-files scope as
        rename by pattern.
        """
        selected = self._selected_video_files()
        targets = selected if selected else self.video_files
        if not targets:
            QMessageBox.information(self, "No Files", "Load some files first.")
            return

        dialog = AutoNumberingDialog(targets, parent=self)
        if dialog.exec():
            self._refresh_table_rows()
            if self._selected_video_files():
                self._on_selection_changed()
            self.status_bar.showMessage(
                f"Auto-numbered {dialog.numbered_count} file(s) -- not yet saved to disk"
            )

    # --- Help ------------------------------------------------------------

    def _on_show_about(self) -> None:
        """Version/about dialog -- now the shared redactor_common
        AboutDialog (Markdown-rendering, matches the epub and mp3
        tools) rather than this project's own plain QMessageBox.about()
        popup, which never rendered ABOUT.md at all despite the file
        existing on disk.
        """
        AboutDialog(
            app_name="The \u0245ideo Redactor",
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
