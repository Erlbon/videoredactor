"""The standard menu skeleton (File, Edit, View, Metadata, Media, Tools, Help)
on the real MainWindow: structure and order, every action still present and
connected, the toolbar, the row context menu, and Remove from List / Clear
List (which this app gained with the skeleton)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtCore import QItemSelectionModel, QPoint  # noqa: E402
from PyQt6.QtGui import QKeySequence  # noqa: E402
from PyQt6.QtWidgets import QFileDialog, QToolBar  # noqa: E402

import gui.main_window as mw  # noqa: E402
from tests.test_batch_operations_and_undo import _app, window  # noqa: E402,F401


def _menus(window) -> dict:
    return {a.text().replace("&", ""): a.menu() for a in window.menuBar().actions() if a.menu()}


def _labels(menu) -> list:
    return [a.text().replace("&", "") for a in menu.actions() if not a.isSeparator()]


def test_headings_are_the_skeleton_in_order(window):
    assert list(_menus(window)) == ["File", "Edit", "View", "Metadata", "Media", "Tools", "Help"]


def test_file_menu_group_order(window):
    shape = ["-" if a.isSeparator() else a.text().replace("&", "") for a in _menus(window)["File"].actions()]
    assert shape == [
        "Open Files…", "Open Folder…", "Import and Convert…", "-",
        "Save All", "-",
        "Rename File…", "Undo Last Rename", "Rename / Export / Move…", "-",
        "Remove from List", "Clear List", "-",
        "Exit",
    ]


def test_other_menus_hold_what_the_spec_says(window):
    menus = _menus(window)
    assert _labels(menus["Edit"]) == [
        "Undo", "Redo", "Apply to 0 Selected", "Redact", "Edit Redact Recipe…",
        "Search and Replace…", "Change Case…", "Auto-Number…",
    ]
    assert _labels(menus["View"]) == [
        "Show Metadata Panel", "Zoom In", "Zoom Out", "Reset Zoom", "Refresh List", "Command Palette…",
    ]
    assert _labels(menus["Metadata"]) == ["Parse Filename…", "Look Up", "Number Episodes…"]
    look_up = next(a for a in menus["Metadata"].actions() if a.menu()).menu()
    assert _labels(look_up) == [
        "TMDB (Movie)…", "TMDB (TV Show)…", "TheTVDB (TV Show)…", "Subtitles (OpenSubtitles)…",
    ]
    assert _labels(menus["Media"]) == [
        "Remux to MP4…", "Convert to MP4 (H.264)…", "Check Files…", "Find Duplicates…",
    ]
    assert _labels(menus["Tools"]) == [
        "API Keys…", "External Tools…", "Columns…", "Genres…", "Languages…",
    ]
    assert _labels(menus["Help"]) == ["Changelog…", "Credits…", "About " + mw.APP_NAME]


# Every action key of the old menus -> where it lives now.
OLD_ACTIONS_NOW = {
    "open_folder": "open_folder", "save_selected": "save_all", "save_all": "save_all",
    "rename_file": "rename_file", "undo_rename": "undo_last_rename", "refresh_list": "refresh_list",
    "exit": "exit", "import_tmdb_movie": "lookup_tmdb_movie", "import_tmdb_tv": "lookup_tmdb_tv",
    "import_tvdb": "lookup_tvdb", "import_from_filename": "parse_filename",
    "import_subtitles": "lookup_subtitles", "import_convert": "import_convert", "remux": "remux",
    "convert_to_mp4": "convert_to_mp4", "rename_by_pattern": "rename_export_move",
    "check_files": "check_files", "find_duplicates": "find_duplicates", "redact": "redact",
    "redact_recipe": "redact_recipe", "case_conversion": "change_case",
    "search_replace": "search_replace", "auto_numbering": "auto_number", "undo": "undo", "redo": "redo",
    "locate_tools": "external_tools", "add_api_keys": "api_keys", "add_remove_columns": "columns",
    "add_remove_languages": "languages", "add_remove_genres": "genres", "about": "about",
    "changelog": "changelog", "credits": "credits",
}


def test_every_old_action_is_still_registered(window):
    registry = window.actions_by_key
    missing = [f"{old} -> {new}" for old, new in OLD_ACTIONS_NOW.items() if new not in registry]
    assert not missing


HANDLERS = {
    "open_files": "_on_open_files", "open_folder": "_on_open_folder", "save_all": "_on_save_all",
    "rename_export_move": "_on_rename_by_pattern",
    "remove_from_list": "_on_remove_from_list", "clear_list": "_on_clear_list", "redact": "_on_redact",
    "redact_recipe": "_on_edit_redact_recipe", "search_replace": "_on_search_replace",
    "change_case": "_on_case_conversion", "auto_number": "_on_auto_numbering", "remux": "_on_remux_selected",
    "convert_to_mp4": "_on_convert_to_mp4", "check_files": "_on_check_files",
    "find_duplicates": "_on_find_duplicates", "api_keys": "_on_add_external_apis",
    "external_tools": "_on_locate_tools", "columns": "_on_open_column_visibility",
    "genres": "_on_open_genres", "languages": "_on_open_languages", "lookup_tvdb": "_on_import_tvdb",
    "lookup_subtitles": "_on_import_subtitles", "import_convert": "_on_import_and_convert",
    "number_episodes": "_on_number_episodes", "refresh_list": "_refresh_list",
    "parse_filename": "_on_import_metadata_from_filename", "rename_file": "rename_selected_file",
    "undo_last_rename": "undo_last_rename", "changelog": "_on_show_changelog",
    "credits": "_on_show_credits", "about": "_on_show_about",
}


def test_actions_are_connected_to_their_handlers(monkeypatch, tmp_path):
    """Triggering each registered action reaches its own handler (handlers are
    patched on the class before the menu is built, so the bound slots are the
    stubs)."""
    import core.config as config

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")
    monkeypatch.setattr(mw.MainWindow, "_restore_last_folder_on_startup", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_check_external_tools_on_startup", lambda self: None)
    called = []
    for handler in set(HANDLERS.values()):
        monkeypatch.setattr(mw.MainWindow, handler, lambda self, *a, name=handler: called.append(name))
    fresh = mw.MainWindow()
    try:
        for key, handler in HANDLERS.items():
            called.clear()
            fresh.actions_by_key[key].trigger()
            assert called == [handler], key
    finally:
        fresh.close()


def test_toolbar_reuses_the_menu_actions_in_order(window):
    toolbar = window.findChildren(QToolBar)[0]
    acts = [a for a in toolbar.actions() if not a.isSeparator()]
    reg = window.actions_by_key
    assert acts[:7] == [reg["open_files"], reg["open_folder"], reg["apply"], reg["save_all"],
                        reg["redact"], reg["undo"], reg["redo"]]
    assert "save" not in reg  # one Save button: Save All
    assert window.zoom.zoom_out_action in acts and window.zoom.zoom_in_action in acts
    assert window.undo_action is reg["undo"] and window.redo_action is reg["redo"]
    assert acts[-1].text() == "Panel"


def test_apply_text_follows_the_selection(window):
    apply_action = window.actions_by_key["apply"]
    window.table.clearSelection()
    window._on_selection_changed()
    assert apply_action.text() == "&Apply to 0 Selected" and not apply_action.isEnabled()
    window.table.selectAll()
    assert apply_action.text() == "&Apply to 2 Selected" and apply_action.isEnabled()
    assert apply_action.shortcut().toString() == "Ctrl+Return"


def test_show_metadata_panel_follows_the_panel(window):
    shown = window.actions_by_key["show_metadata_panel"]
    assert shown.isCheckable() and shown.isChecked()
    shown.trigger()  # untick -> collapse
    assert window._panel_collapser.is_collapsed() and not shown.isChecked()
    window._toggle_tag_panel()  # the toolbar button / splitter path
    assert not window._panel_collapser.is_collapsed() and shown.isChecked()


def test_zoom_shortcuts_are_unambiguous(window):
    reg = window.actions_by_key
    assert reg["zoom_in"].shortcut().toString() == "Ctrl++" and reg["zoom_out"].shortcut().toString() == "Ctrl+-"
    assert window.zoom.zoom_in_action.shortcut().isEmpty() and window.zoom.zoom_out_action.shortcut().isEmpty()
    before = window.table.font().pointSize()
    reg["zoom_in"].trigger()
    assert window.table.font().pointSize() == before + 1
    reg["reset_zoom"].trigger()
    assert window.table.font().pointSize() == before


# --- Remove from List / Clear List ---------------------------------------------


def _select_rows(window, rows):
    window.table.clearSelection()
    model = window.table.selectionModel()
    for row in rows:
        model.select(window.table.model().index(row, 0),
                     QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
    window._on_selection_changed()


def test_remove_from_list_drops_only_the_selected_files(window, tmp_path):
    _select_rows(window, [0])
    selected = window._selected_video_files()
    window.actions_by_key["remove_from_list"].trigger()
    assert window.table.rowCount() == 1 and len(window.video_files) == 1
    assert window.video_files[0] is not selected[0]
    assert (tmp_path / "The Office S01E01.mkv").exists() and (tmp_path / "The Office S01E02.mkv").exists()
    assert window.actions_by_key["remove_from_list"].shortcut() == QKeySequence("Delete")


def test_remove_with_nothing_selected_says_so(window):
    window.table.clearSelection()
    window._on_remove_from_list()
    assert len(window.video_files) == 2
    assert window.status_bar.currentMessage() == "No files selected"


def test_remove_asks_before_discarding_unsaved_edits(window, monkeypatch):
    window.video_files[0].dirty = True
    _select_rows(window, [0, 1])
    asked = []
    monkeypatch.setattr(mw.MainWindow, "_confirm_discard", lambda self, what: asked.append(what) or False)
    window._on_remove_from_list()
    assert len(window.video_files) == 2 and "unsaved changes on 1" in asked[0]
    monkeypatch.setattr(mw.MainWindow, "_confirm_discard", lambda self, what: True)
    window._on_remove_from_list()
    assert window.video_files == [] and window.table.rowCount() == 0


def test_remove_clears_the_undo_stack(window):
    window._push_undo("Bulk Edit", list(window.video_files))
    assert window.undo_action.isEnabled()
    _select_rows(window, [0])
    window._on_remove_from_list()
    assert not window.undo_action.isEnabled()


def test_clear_list_empties_the_list_and_confirms_unsaved_edits(window, monkeypatch):
    window.video_files[1].dirty = True
    monkeypatch.setattr(mw.MainWindow, "_confirm_discard", lambda self, what: False)
    window._on_clear_list()
    assert len(window.video_files) == 2  # declined
    monkeypatch.setattr(mw.MainWindow, "_confirm_discard", lambda self, what: True)
    window.actions_by_key["clear_list"].trigger()
    assert window.video_files == [] and window.table.rowCount() == 0
    assert not window.apply_action.isEnabled()
    window._on_clear_list()
    assert window.status_bar.currentMessage() == "The list is already empty"


def test_clear_list_does_not_ask_when_nothing_is_unsaved(window, monkeypatch):
    monkeypatch.setattr(mw.MainWindow, "_confirm_discard", lambda self, what: pytest.fail("asked"))
    window._on_clear_list()
    assert window.video_files == []


# --- Open Files ------------------------------------------------------------------


def test_open_files_adds_to_the_list_and_skips_duplicates(window, monkeypatch, tmp_path):
    existing = window.video_files[0].path
    new = tmp_path / "Another Movie (2001).mp4"
    new.write_bytes(b"")
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *a, **k: ([str(existing), str(new)], ""))
    window._on_open_files()
    assert [vf.path for vf in window.video_files][-1] == new
    assert len(window.video_files) == 3 and window.table.rowCount() == 3
    assert "1 already in the list" in window.status_bar.currentMessage()


def test_open_files_cancelled_changes_nothing(window, monkeypatch):
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *a, **k: ([], ""))
    window._on_open_files()
    assert len(window.video_files) == 2


# --- Context menu ------------------------------------------------------------------


def test_context_menu_follows_the_skeleton(window, monkeypatch):
    seen = {}

    def fake_show(win, table, pos, get_selected_items, get_path, extra_items=None):
        seen["single"] = extra_items(window.video_files[:1])
        seen["multi"] = extra_items(list(window.video_files))

    monkeypatch.setattr(mw, "show_table_context_menu", fake_show)
    window._show_table_context_menu(QPoint(0, 0))
    reg = window.actions_by_key

    def shape(items):
        out = []
        for item in items:
            if isinstance(item, mw.Separator):
                out.append("-")
            elif isinstance(item, mw.Submenu):
                out.append(item.text)
            else:
                out.append(item.text().replace("&", ""))
        return out

    assert shape(seen["single"]) == [
        "-", "Rename File…", "-", "Look Up", "Organize", "-", "Redact", "-", "Remove from List",
    ]
    assert shape(seen["multi"]) == ["-", "Look Up", "Organize", "-", "Redact", "-", "Remove from List"]
    organize = next(i for i in seen["single"] if isinstance(i, mw.Submenu) and i.text == "Organize")
    assert organize.items[0] is reg["rename_export_move"]
    assert organize.items[1].text == "Number Episodes…"
    assert reg["redact"] in seen["single"] and reg["remove_from_list"] in seen["single"]


def test_number_episodes_menu_entry_needs_a_selection(window, monkeypatch):
    window.table.clearSelection()
    window._on_number_episodes()
    assert window.status_bar.currentMessage() == "No files selected"
    numbered = []
    monkeypatch.setattr(mw.MainWindow, "_quick_number_episodes", lambda self, files: numbered.append(len(files)))
    window.table.selectAll()
    window._on_number_episodes()
    assert numbered == [2]


# --- Command palette and lint -----------------------------------------------------

# Violations the lint reports on purpose: Save All keeps its old Ctrl+Shift+S as
# a secondary shortcut for one release (there is no Save As in this app, so the
# key is not ambiguous). Delete this entry, and the alias, in the next release.
DOCUMENTED_LINT_EXCEPTIONS = {
    "File > Save All is bound to Ctrl+Shift+S: Ctrl+Shift+S is Save As (platform standard)",
}


def test_menu_bar_passes_the_skeleton_lint(window):
    from redactor_common.gui.menu_lint import lint_menu_bar

    assert set(lint_menu_bar(window)) == DOCUMENTED_LINT_EXCEPTIONS


def test_command_palette_is_on_ctrl_k_and_finds_every_action(window):
    from redactor_common.gui.command_palette import CommandPalette, collect_commands

    action = window.actions_by_key["command_palette"]
    assert action.shortcut() == QKeySequence("Ctrl+K")
    assert isinstance(window.command_palette, CommandPalette)
    titles = {c.title for c in collect_commands(window, window.actions_by_key, exclude=action)}
    for wanted in ("Open Files", "Remove from List", "Redact", "Remux to MP4", "TMDB (Movie)",
                   "Number Episodes", "External Tools", "Reset Zoom"):
        assert wanted in titles
    window.command_palette.show_palette()
    window.command_palette.set_filter("remux")
    assert [c.title for c in window.command_palette.visible_commands()][0] == "Remux to MP4"
    window.command_palette.close()


# --- Shortcuts ---------------------------------------------------------------------


def _keys(action) -> list:
    return [k.toString() for k in action.shortcuts()]


def test_skeleton_shortcuts_and_one_release_aliases(window):
    reg = window.actions_by_key
    assert _keys(reg["open_files"]) == ["Ctrl+O"]
    assert _keys(reg["open_folder"]) == ["Ctrl+Shift+O"]
    # Save All is Ctrl+Shift+A; the old Save Selected and Save All Changed keys
    # still work, and all of them save every changed file.
    assert _keys(reg["save_all"]) == ["Ctrl+Shift+A", "Ctrl+S", "Ctrl+Shift+S"]
    assert _keys(reg["lookup_subtitles"]) == ["Ctrl+Shift+L"]  # was Ctrl+Shift+O
    assert _keys(reg["lookup_tmdb_movie"]) == ["Ctrl+M"] and _keys(reg["lookup_tmdb_tv"]) == ["Ctrl+T"]
    assert _keys(reg["lookup_tvdb"]) == ["Ctrl+Shift+T"] and _keys(reg["convert_to_mp4"]) == ["Ctrl+Shift+C"]
    assert _keys(reg["refresh_list"]) == ["F5"]  # Ctrl+R is Remux here
    assert _keys(reg["remux"]) == ["Ctrl+R"]
    assert _keys(reg["apply"]) == ["Ctrl+Return"]
    assert _keys(reg["redact"]) == ["Ctrl+Shift+E"]
    assert _keys(reg["command_palette"]) == ["Ctrl+K"]
    assert _keys(reg["about"]) == []  # F1 is not About any more


def test_no_key_sequence_is_bound_to_two_actions(window):
    """A sequence on two enabled actions in one window is ambiguous and fires
    neither; covers the menu bar, toolbar and zoom buttons together."""
    from PyQt6.QtGui import QAction

    seen: dict = {}
    for act in window.findChildren(QAction):
        for seq in _keys(act):
            seen.setdefault(seq, set()).add(id(act))
    assert {seq for seq, owners in seen.items() if len(owners) > 1} == set()
    assert window.zoom.zoom_in_action.shortcuts() == [] and window.zoom.zoom_out_action.shortcuts() == []


def test_old_save_keys_trigger_save_all(window, monkeypatch):
    calls = []
    monkeypatch.setattr(mw.MainWindow, "_save_files", lambda self, files, skipped_error_count=0: calls.append(files))
    window.video_files[1].dirty = True
    window.actions_by_key["save_all"].trigger()
    assert calls == [[window.video_files[1]]]  # only the changed file, selection or not


def test_save_all_with_nothing_changed_says_so(window):
    window.actions_by_key["save_all"].trigger()
    assert window.status_bar.currentMessage() == "Nothing to save -- no unsaved changes"
